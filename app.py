import os, re, json, random, secrets, string, io
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import urlparse

from flask import (Flask, render_template, redirect, url_for, request,
                   jsonify, session, abort, send_file, flash, g)
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_migrate import Migrate
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv

import redis as redis_lib
import qrcode
from qrcode.image.svg import SvgImage
try:
    import geoip2.database
    import geoip2.errors
    GEOIP_AVAILABLE = True
except ImportError:
    GEOIP_AVAILABLE = False
try:
    from user_agents import parse as ua_parse
    UA_AVAILABLE = True
except ImportError:
    UA_AVAILABLE = False
import bcrypt
import firebase_admin
from firebase_admin import credentials, auth

load_dotenv()

# Initialize Firebase Admin
if not firebase_admin._apps:
    service_account_path = os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON_PATH')
    service_account_json_str = os.environ.get('FIREBASE_SERVICE_ACCOUNT_JSON')
    
    if service_account_path and os.path.exists(service_account_path):
        cred = credentials.Certificate(service_account_path)
        firebase_admin.initialize_app(cred)
    elif service_account_json_str:
        try:
            cert_dict = json.loads(service_account_json_str)
            cred = credentials.Certificate(cert_dict)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            print(f"Error parsing FIREBASE_SERVICE_ACCOUNT_JSON: {e}")
            firebase_admin.initialize_app()
    else:
        try:
            firebase_admin.initialize_app()
        except Exception as e:
            cred = credentials.Certificate({
                "type": "service_account",
                "project_id": os.environ.get('FIREBASE_PROJECT_ID', 'drizl-dev-mock'),
                "private_key_id": "mock-private-key-id",
                "private_key": "-----BEGIN PRIVATE KEY-----\nMOCK\n-----END PRIVATE KEY-----\n",
                "client_email": "mock@drizl-dev-mock.iam.gserviceaccount.com",
                "client_id": "123456789",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/o/oauth2/auth",
                "client_x509_cert_url": "https://www.googleapis.com/metadata/x509/accounts"
            })
            firebase_admin.initialize_app(cred)

from config import config
from models import db, User, Link, Click

# ── App factory ──────────────────────────────────────────────────
app = Flask(__name__)
env = os.environ.get('FLASK_ENV', 'development')
app.config.from_object(config.get(env, config['default']))

db.init_app(app)
migrate = Migrate(app, db)
CORS(app, resources={r"/api/*": {"origins": app.config.get('FRONTEND_URL', '*')}})

# ── Redis availability check for Limiter fallback ──
_redis_url = app.config.get('REDIS_URL', 'redis://localhost:6379')
_limiter_storage = _redis_url
try:
    _test_c = redis_lib.from_url(_redis_url, socket_connect_timeout=1)
    _test_c.ping()
except Exception:
    _limiter_storage = "memory://"
    print("Redis unavailable for Limiter -- falling back to memory storage")

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per minute"],
    storage_uri=_limiter_storage,
)

login_manager = LoginManager(app)
login_manager.login_view = 'login_page'

@app.context_processor
def inject_firebase_config():
    return {
        'firebase_config': {
            'apiKey': os.environ.get('FIREBASE_API_KEY', ''),
            'authDomain': os.environ.get('FIREBASE_AUTH_DOMAIN', ''),
            'projectId': os.environ.get('FIREBASE_PROJECT_ID', ''),
            'storageBucket': os.environ.get('FIREBASE_STORAGE_BUCKET', ''),
            'messagingSenderId': os.environ.get('FIREBASE_MESSAGING_SENDER_ID', ''),
            'appId': os.environ.get('FIREBASE_APP_ID', '')
        }
    }

# ── Redis client ──────────────────────────────────────────────────
try:
    _redis = redis_lib.from_url(app.config.get('REDIS_URL', 'redis://localhost:6379'),
                                 decode_responses=True, socket_connect_timeout=2)
    _redis.ping()
    REDIS_OK = True
except Exception:
    _redis = None
    REDIS_OK = False
    print("Redis unavailable -- using DB-only mode (slower redirects)")

LINK_TTL = app.config.get('LINK_CACHE_TTL', 3600)

def cache_get(key):
    if not REDIS_OK: return None
    try: return json.loads(_redis.get(key) or 'null')
    except: return None

def cache_set(key, value):
    if not REDIS_OK: return
    try: _redis.setex(key, LINK_TTL, json.dumps(value))
    except: pass

def cache_del(key):
    if not REDIS_OK: return
    try: _redis.delete(key)
    except: pass

# ── Login manager ─────────────────────────────────────────────────
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)

# ── Helpers ───────────────────────────────────────────────────────
NANOID_CHARS = string.ascii_letters + string.digits

def normalize_url(url):
    url = (url or '').strip()
    if not url:
        return url
    parsed = urlparse(url)
    if not parsed.scheme:
        first_part = url.split('/')[0]
        if '.' in first_part or first_part.startswith('localhost'):
            url = 'https://' + url
    return url

def parse_iso_datetime(iso_str):
    if not iso_str:
        return None
    dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt

def gen_short_code(length=6):
    return ''.join(random.choices(NANOID_CHARS, k=length))

def make_unique_code():
    for _ in range(10):
        code = gen_short_code()
        if not Link.query.filter_by(short_code=code).first():
            return code
    raise Exception("Could not generate unique code")

def get_short_url(code):
    base = app.config.get('SHORT_URL_BASE', request.host_url.rstrip('/'))
    return f"{base}/{code}"

def get_ip():
    return (request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
            or request.remote_addr or '')

def get_geo(ip):
    if not GEOIP_AVAILABLE: return None, None
    try:
        db_path = os.path.join(os.path.dirname(__file__), 'GeoLite2-City.mmdb')
        if not os.path.exists(db_path): return None, None
        with geoip2.database.Reader(db_path) as reader:
            r = reader.city(ip)
            return r.country.iso_code, r.city.name
    except: return None, None

def parse_ua(ua_string):
    if not UA_AVAILABLE:
        ua_lower = (ua_string or '').lower()
        device = 'mobile' if 'mobile' in ua_lower else ('tablet' if 'tablet' in ua_lower else 'desktop')
        return device, 'Unknown', 'Unknown'
    ua = ua_parse(ua_string or '')
    device = 'tablet' if ua.is_tablet else ('mobile' if ua.is_mobile else 'desktop')
    return device, ua.browser.family or 'Unknown', ua.os.family or 'Unknown'

def log_click(link_id, variant=None):
    """Async-like click logging — called before redirect."""
    try:
        ip = get_ip()
        country, city = get_geo(ip)
        ua_str = request.headers.get('User-Agent', '')
        device, browser, os_name = parse_ua(ua_str)
        referrer = (request.referrer or '')[:500]
        click = Click(
            link_id=link_id, country=country, city=city,
            device=device, browser=browser, os=os_name,
            referrer=referrer or None, ip_address=ip[:45] if ip else None,
            variant=variant
        )
        db.session.add(click)
        db.session.query(Link).filter_by(id=link_id).update({Link.click_count: Link.click_count + 1})
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Click log error: {e}")



# ════════════════════════════════════════════════════════════════
# PAGE ROUTES (Jinja2 Templates)
# ════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return render_template('landing.html')

@app.route('/login')
def login_page():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/register')
def register_page():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('register.html')

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', user=current_user)

@app.route('/dashboard/links')
@login_required
def my_links():
    return render_template('links.html', user=current_user)

@app.route('/dashboard/analytics/<link_id>')
@login_required
def analytics(link_id):
    link = Link.query.filter_by(id=link_id, user_id=current_user.id).first_or_404()
    return render_template('analytics.html', link=link, user=current_user)



@app.route('/dashboard/settings')
@login_required
def settings_page():
    return render_template('settings.html', user=current_user)

@app.route('/about')
def about_page():
    return render_template('about_developer.html')

# ════════════════════════════════════════════════════════════════
# REDIRECT ENGINE  /:code
# ════════════════════════════════════════════════════════════════
RESERVED = {'api', 'login', 'register', 'dashboard', 'static', 'health',
            'admin', 'robots.txt', 'favicon.ico', 'about'}

@app.route('/<code>', methods=['GET', 'POST'])
@limiter.exempt
def redirect_link(code):
    if code in RESERVED:
        abort(404)

    # Password unlock via POST
    if request.method == 'POST':
        return handle_password_unlock(code)

    # 1. Redis cache
    cached = cache_get(f'link:{code}')
    if cached:
        link_data = cached
        link_id = link_data['id']
        is_active = link_data.get('is_active', True)
        expires_at_str = link_data.get('expires_at')
        expires_at = datetime.fromisoformat(expires_at_str) if expires_at_str else None
        max_clicks = link_data.get('max_clicks')
        click_count = link_data.get('click_count', 0)
        password_hash = link_data.get('password_hash')
        original_url = link_data.get('original_url')
        geo_rules = link_data.get('geo_rules')
        device_rules = link_data.get('device_rules')
        is_ab_test = link_data.get('is_ab_test', False)
        url_b = link_data.get('url_b')
        split_ratio = link_data.get('split_ratio', 50)
    else:
        link_orm = Link.query.filter_by(short_code=code).first()
        if not link_orm:
            return render_template('404.html'), 404
        cache_set(f'link:{code}', link_orm.to_dict())
        link_id = link_orm.id
        is_active = link_orm.is_active
        expires_at = link_orm.expires_at
        max_clicks = link_orm.max_clicks
        click_count = link_orm.click_count
        password_hash = link_orm.password_hash
        original_url = link_orm.original_url
        geo_rules = link_orm.geo_rules
        device_rules = link_orm.device_rules
        is_ab_test = link_orm.is_ab_test
        url_b = link_orm.url_b
        split_ratio = link_orm.split_ratio

    # 2. Active check
    if not is_active:
        return render_template('error.html', title='Link Deactivated',
                               msg='This link has been deactivated.'), 410

    # 3. Expiry check
    if expires_at:
        if expires_at.tzinfo:
            expires_at = expires_at.astimezone(timezone.utc).replace(tzinfo=None)
        if expires_at < datetime.utcnow():
            return render_template('error.html', title='Link Expired',
                                   msg='This short link has expired.'), 410

    # 4. Max clicks
    if max_clicks and click_count >= max_clicks:
        return render_template('error.html', title='Link Exhausted',
                               msg='This link has reached its click limit.'), 410

    # 5. Password protection
    if password_hash:
        pw = request.args.get('pw') or request.cookies.get(f'pw_{code}')
        if not pw or not bcrypt.checkpw(pw.encode(), password_hash.encode()):
            return render_template('password_gate.html', code=code,
                                   invalid=bool(pw)), 200

    # 6. Parse UA & geo
    ua_str  = request.headers.get('User-Agent', '')
    device, browser, os_name = parse_ua(ua_str)
    ip      = get_ip()
    country, city = get_geo(ip)

    # 7. Determine target URL
    target_url = original_url

    # Geo targeting
    if geo_rules and country:
        for rule in geo_rules:
            if rule.get('country') == country and rule.get('url'):
                target_url = rule['url']; break

    # Device targeting
    if device_rules:
        for rule in device_rules:
            if rule.get('device', '').lower() == device.lower() and rule.get('url'):
                target_url = rule['url']; break

    # 8. A/B routing
    variant = None
    if is_ab_test and url_b:
        ratio = split_ratio or 50
        variant = 'A' if random.random() * 100 < ratio else 'B'
        if variant == 'B':
            target_url = url_b

    # 9. Log click
    log_click(link_id, variant=variant)

    # Invalidate cache so click_count is fresh next time
    cache_del(f'link:{code}')

    return redirect(target_url, 302)

def handle_password_unlock(code):
    pw = request.form.get('password', '')
    link = Link.query.filter_by(short_code=code).first()
    if not link or not link.password_hash:
        abort(404)
    if link.check_password(pw):
        resp = redirect(f'/{code}?pw={pw}')
        resp.set_cookie(f'pw_{code}', pw, max_age=3600, httponly=True)
        return resp
    return render_template('password_gate.html', code=code, invalid=True), 200

# ════════════════════════════════════════════════════════════════
# AUTH API
# ════════════════════════════════════════════════════════════════

@app.route('/api/auth/login', methods=['POST'])
@limiter.limit("20 per 15 minutes")
def api_login():
    data = request.get_json() or {}
    id_token = data.get('id_token')
    if not id_token:
        return jsonify({'error': 'Firebase ID token required'}), 400

    decoded_token = None
    if app.config.get('DEBUG', True) and id_token.startswith('mock-token-'):
        token_data = id_token[len('mock-token-'):]
        parts = token_data.split('|')
        email = parts[0] if parts[0] else "mock@example.com"
        name = parts[1] if len(parts) > 1 and parts[1] else None
        import hashlib
        uid = "mock_" + hashlib.md5(email.encode()).hexdigest()[:24]
        decoded_token = {
            'uid': uid,
            'email': email,
            'name': name
        }
    else:
        try:
            decoded_token = auth.verify_id_token(id_token)
        except Exception as e:
            return jsonify({'error': f'Authentication failed: {str(e)}'}), 401

    uid = decoded_token['uid']
    email = decoded_token.get('email', '')
    name = decoded_token.get('name', '')

    user = db.session.get(User, uid)
    is_new = False
    if not user:
        is_new = True
        username = email.split('@')[0] if email else f"user_{uid[:8]}"
        base_username = username
        counter = 1
        while User.query.filter_by(username=username).first():
            username = f"{base_username}_{counter}"
            counter += 1

        user = User(
            id=uid,
            email=email or f"{uid}@drizl.local",
            username=username,
            display_name=None # Initialize to None so they get prompted "What should I call you?"
        )
        db.session.add(user)
        db.session.commit()

    login_user(user, remember=True)
    status_code = 201 if is_new else 200
    return jsonify({
        'user': user.to_dict(),
        'message': 'Account created!' if is_new else 'Logged in!'
    }), status_code

@app.route('/api/auth/register', methods=['POST'])
@limiter.limit("20 per 15 minutes")
def api_register():
    return api_login()

@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    logout_user()
    return jsonify({'message': 'Logged out'})

@app.route('/api/auth/me')
@login_required
def api_me():
    return jsonify({'user': current_user.to_dict()})

# ════════════════════════════════════════════════════════════════
# LINKS API
# ════════════════════════════════════════════════════════════════

@app.route('/api/links', methods=['GET'])
@login_required
def api_list_links():
    search = request.args.get('search', '')
    status = request.args.get('status', '')
    page   = int(request.args.get('page', 1))
    limit  = int(request.args.get('limit', 20))

    q = Link.query.filter_by(user_id=current_user.id)
    if search:
        like = f'%{search}%'
        q = q.filter(db.or_(Link.short_code.ilike(like),
                             Link.original_url.ilike(like),
                             Link.title.ilike(like)))
    q = q.order_by(Link.created_at.desc())
    total  = q.count()
    links  = q.offset((page - 1) * limit).limit(limit).all()
    result = [l.to_dict() for l in links]
    # Filter by status after fetching (computed property)
    if status and status != 'all':
        result = [l for l in result if l['status'] == status]
    return jsonify({'links': result, 'total': total, 'page': page, 'limit': limit})

@app.route('/api/public/shorten', methods=['POST'])
def api_public_shorten():
    if not current_user.is_authenticated:
        guest_count = session.get('guest_shortens', 0)
        if guest_count >= 3:
            return jsonify({'error': 'Free limit reached. Please sign in or register to shorten more links!'}), 403

    data = request.get_json() or {}
    original_url = normalize_url((data.get('original_url') or data.get('url') or '').strip())
    if not original_url:
        return jsonify({'error': 'URL is required'}), 400
    parsed = urlparse(original_url)
    if parsed.scheme not in ('http', 'https'):
        return jsonify({'error': 'Invalid URL scheme. Only HTTP and HTTPS are allowed.'}), 400

    short_code = make_unique_code()
    link = Link(
        user_id=current_user.id if current_user.is_authenticated else None,
        short_code=short_code,
        original_url=original_url,
        custom_slug=False
    )
    db.session.add(link)
    db.session.commit()

    if not current_user.is_authenticated:
        session['guest_shortens'] = guest_count + 1

    return jsonify({
        'link': link.to_dict(),
        'short_url': get_short_url(short_code),
        'guest_count': session.get('guest_shortens', 0) if not current_user.is_authenticated else None
    }), 201

@app.route('/api/links', methods=['POST'])
@login_required
def api_create_link():
    data = request.get_json()
    
    # Normalize and validate destination URL
    original_url = normalize_url((data.get('original_url') or data.get('url') or '').strip())
    if not original_url:
        return jsonify({'error': 'URL is required'}), 400
    parsed = urlparse(original_url)
    if parsed.scheme not in ('http', 'https'):
        return jsonify({'error': 'Invalid URL scheme. Only HTTP and HTTPS are allowed.'}), 400

    # Custom slug validation
    custom_slug = data.get('custom_slug') or data.get('slug')
    if custom_slug:
        if not re.match(r'^[a-zA-Z0-9_-]{2,30}$', custom_slug):
            return jsonify({'error': 'Invalid slug format'}), 400
        if Link.query.filter_by(short_code=custom_slug).first():
            return jsonify({'error': 'Slug already taken'}), 409
        short_code = custom_slug
        is_custom  = True
    else:
        short_code = make_unique_code()
        is_custom  = False

    # A/B testing URL validation
    is_ab_test = bool(data.get('is_ab_test', False))
    url_b = data.get('url_b')
    if is_ab_test and url_b:
        url_b = normalize_url(url_b.strip())
        parsed_b = urlparse(url_b)
        if parsed_b.scheme not in ('http', 'https'):
            return jsonify({'error': 'Invalid URL B scheme. Only HTTP and HTTPS are allowed.'}), 400
            
    # Split ratio validation
    split_ratio = data.get('split_ratio', 50)
    try:
        split_ratio = int(split_ratio)
        if not (0 <= split_ratio <= 100): raise ValueError()
    except (TypeError, ValueError):
        return jsonify({'error': 'split_ratio must be an integer between 0 and 100'}), 400

    # Max clicks validation
    max_clicks = data.get('max_clicks')
    if max_clicks is not None:
        try:
            max_clicks = int(max_clicks)
            if max_clicks < 0: raise ValueError()
        except (TypeError, ValueError):
            return jsonify({'error': 'max_clicks must be a positive integer'}), 400

    # Expiry validation
    expires_at = None
    if data.get('expires_at'):
        try:
            expires_at = parse_iso_datetime(data['expires_at'])
        except Exception:
            return jsonify({'error': 'Invalid expires_at format. Must be an ISO 8601 datetime.'}), 400

    # Geo rules validation
    geo_rules = data.get('geo_rules')
    if geo_rules is not None:
        if not isinstance(geo_rules, list) or not all(isinstance(r, dict) for r in geo_rules):
            return jsonify({'error': 'geo_rules must be a list of objects'}), 400
        for rule in geo_rules:
            if 'url' in rule:
                rule['url'] = normalize_url(rule['url'])
                if urlparse(rule['url']).scheme not in ('http', 'https'):
                    return jsonify({'error': 'Invalid geo rule redirect URL scheme.'}), 400

    # Device rules validation
    device_rules = data.get('device_rules')
    if device_rules is not None:
        if not isinstance(device_rules, list) or not all(isinstance(r, dict) for r in device_rules):
            return jsonify({'error': 'device_rules must be a list of objects'}), 400
        for rule in device_rules:
            if 'url' in rule:
                rule['url'] = normalize_url(rule['url'])
                if urlparse(rule['url']).scheme not in ('http', 'https'):
                    return jsonify({'error': 'Invalid device rule redirect URL scheme.'}), 400

    link = Link(
        user_id=current_user.id, short_code=short_code,
        original_url=original_url, custom_slug=is_custom,
        title=data.get('title'),
        is_ab_test=is_ab_test,
        url_b=url_b,
        split_ratio=split_ratio,
        max_clicks=max_clicks,
        expires_at=expires_at,
        geo_rules=geo_rules,
        device_rules=device_rules,
    )
    if data.get('password'):
        link.set_password(data['password'])

    db.session.add(link)
    db.session.commit()
    return jsonify({'link': link.to_dict(), 'short_url': get_short_url(short_code)}), 201

@app.route('/api/links/<link_id>', methods=['GET'])
@login_required
def api_get_link(link_id):
    link = Link.query.filter_by(id=link_id, user_id=current_user.id).first_or_404()
    return jsonify({'link': link.to_dict(), 'short_url': get_short_url(link.short_code)})

@app.route('/api/links/<link_id>', methods=['PATCH'])
@login_required
def api_update_link(link_id):
    link = Link.query.filter_by(id=link_id, user_id=current_user.id).first_or_404()
    data = request.get_json()
    
    if 'original_url' in data:
        url = normalize_url(data['original_url'])
        if not url:
            return jsonify({'error': 'URL cannot be empty'}), 400
        if urlparse(url).scheme not in ('http', 'https'):
            return jsonify({'error': 'Invalid URL scheme.'}), 400
        link.original_url = url
        
    if 'title' in data:
        link.title = data['title']
        
    if 'is_active' in data:
        link.is_active = bool(data['is_active'])
        
    if 'is_ab_test' in data:
        link.is_ab_test = bool(data['is_ab_test'])
        
    if 'url_b' in data:
        url_b = data['url_b']
        if url_b:
            url_b = normalize_url(url_b.strip())
            if urlparse(url_b).scheme not in ('http', 'https'):
                return jsonify({'error': 'Invalid URL B scheme.'}), 400
        link.url_b = url_b
        
    if 'split_ratio' in data:
        try:
            ratio = int(data['split_ratio'])
            if not (0 <= ratio <= 100): raise ValueError()
            link.split_ratio = ratio
        except:
            return jsonify({'error': 'split_ratio must be an integer between 0 and 100.'}), 400
            
    if 'max_clicks' in data:
        try:
            mc = int(data['max_clicks']) if data['max_clicks'] is not None else None
            if mc is not None and mc < 0: raise ValueError()
            link.max_clicks = mc
        except:
            return jsonify({'error': 'max_clicks must be a positive integer.'}), 400
            
    if 'expires_at' in data:
        try:
            link.expires_at = parse_iso_datetime(data['expires_at']) if data['expires_at'] else None
        except Exception:
            return jsonify({'error': 'Invalid expires_at format. Must be an ISO 8601 datetime.'}), 400
            
    if 'geo_rules' in data:
        gr = data['geo_rules']
        if gr is not None:
            if not isinstance(gr, list) or not all(isinstance(r, dict) for r in gr):
                return jsonify({'error': 'geo_rules must be a list of objects.'}), 400
            for rule in gr:
                if 'url' in rule:
                    rule['url'] = normalize_url(rule['url'])
                    if urlparse(rule['url']).scheme not in ('http', 'https'):
                        return jsonify({'error': 'Invalid geo rule redirect URL scheme.'}), 400
        link.geo_rules = gr
        
    if 'device_rules' in data:
        dr = data['device_rules']
        if dr is not None:
            if not isinstance(dr, list) or not all(isinstance(r, dict) for r in dr):
                return jsonify({'error': 'device_rules must be a list of objects.'}), 400
            for rule in dr:
                if 'url' in rule:
                    rule['url'] = normalize_url(rule['url'])
                    if urlparse(rule['url']).scheme not in ('http', 'https'):
                        return jsonify({'error': 'Invalid device rule redirect URL scheme.'}), 400
        link.device_rules = dr
        
    if 'password' in data:
        link.set_password(data['password']) if data['password'] else setattr(link, 'password_hash', None)
        
    db.session.commit()
    cache_del(f'link:{link.short_code}')
    return jsonify({'link': link.to_dict()})

@app.route('/api/links/<link_id>', methods=['DELETE'])
@login_required
def api_delete_link(link_id):
    link = Link.query.filter_by(id=link_id, user_id=current_user.id).first_or_404()
    cache_del(f'link:{link.short_code}')
    db.session.delete(link)
    db.session.commit()
    return jsonify({'message': 'Link deleted'})

@app.route('/api/links/<link_id>/qr')
@login_required
def api_qr(link_id):
    link = Link.query.filter_by(id=link_id, user_id=current_user.id).first_or_404()
    short_url = get_short_url(link.short_code)
    fmt = request.args.get('format', 'png')
    qr = qrcode.QRCode(version=1, box_size=10, border=2,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(short_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color='#7C6FFF', back_color='white')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png',
                     download_name=f'qr-{link.short_code}.png',
                     as_attachment=fmt == 'download')

# ════════════════════════════════════════════════════════════════
# ANALYTICS API
# ════════════════════════════════════════════════════════════════

@app.route('/api/analytics')
@login_required
def api_analytics_summary():
    links = Link.query.filter_by(user_id=current_user.id).all()
    since_7d = datetime.utcnow() - timedelta(days=7)
    recent = db.session.query(db.func.count(Click.id)).join(Link).filter(
        Link.user_id == current_user.id, Click.clicked_at >= since_7d).scalar() or 0
    total_clicks = sum(l.click_count for l in links)
    top = max(links, key=lambda l: l.click_count, default=None)
    return jsonify({
        'total_links':  len(links),
        'total_clicks': total_clicks,
        'active_links': sum(1 for l in links if l.status == 'active'),
        'top_link': {'short_code': top.short_code, 'clicks': top.click_count} if top else None,
        'recent_clicks': recent,
    })

@app.route('/api/analytics/<link_id>')
@login_required
def api_analytics_link(link_id):
    link = Link.query.filter_by(id=link_id, user_id=current_user.id).first_or_404()
    range_map = {'7d': 7, '30d': 30, '90d': 90}
    rng = request.args.get('range', '30d')
    days = range_map.get(rng)
    since = datetime.utcnow() - timedelta(days=days) if days else None

    q = Click.query.filter_by(link_id=link.id)
    if since: q = q.filter(Click.clicked_at >= since)
    clicks = q.order_by(Click.clicked_at.asc()).all()

    total = len(clicks)

    # By day
    day_map = {}
    for c in clicks:
        d = c.clicked_at.strftime('%Y-%m-%d')
        day_map[d] = day_map.get(d, 0) + 1
    by_day = [{'date': d, 'count': n} for d, n in sorted(day_map.items())]

    # Countries
    cnt = {}
    for c in clicks:
        if c.country: cnt[c.country] = cnt.get(c.country, 0) + 1
    top_countries = sorted(cnt.items(), key=lambda x: x[1], reverse=True)[:10]
    top_countries = [{'country': k, 'count': v} for k, v in top_countries]

    # Devices
    dev = {}
    for c in clicks:
        d = c.device or 'desktop'; dev[d] = dev.get(d, 0) + 1
    device_split = [{'device': k, 'count': v,
                     'pct': round(v/total*100) if total else 0}
                    for k, v in dev.items()]

    # Browsers
    br = {}
    for c in clicks:
        b = c.browser or 'Unknown'; br[b] = br.get(b, 0) + 1
    browser_split = sorted(br.items(), key=lambda x: x[1], reverse=True)[:6]
    browser_split = [{'browser': k, 'count': v} for k, v in browser_split]

    # Referrers
    ref = {}
    for c in clicks:
        r = 'Direct'
        if c.referrer:
            try: r = urlparse(c.referrer).netloc.replace('www.', '') or 'Direct'
            except: pass
        ref[r] = ref.get(r, 0) + 1
    top_referrers = sorted(ref.items(), key=lambda x: x[1], reverse=True)[:8]
    top_referrers = [{'referrer': k, 'count': v} for k, v in top_referrers]

    # A/B
    ab = None
    if link.is_ab_test:
        a_c = sum(1 for c in clicks if c.variant == 'A')
        b_c = sum(1 for c in clicks if c.variant == 'B')
        tot = a_c + b_c
        ab = {'url_a': link.original_url, 'url_b': link.url_b,
              'a_clicks': a_c, 'b_clicks': b_c, 'total': tot,
              'a_pct': round(a_c/tot*100) if tot else 50,
              'b_pct': round(b_c/tot*100) if tot else 50}

    top_country = top_countries[0]['country'] if top_countries else None
    top_device  = max(device_split, key=lambda x: x['count'])['device'] if device_split else None

    return jsonify({
        'link': link.to_dict(),
        'summary': {'total_clicks': total, 'top_country': top_country,
                    'top_device': top_device, 'range': rng},
        'by_day': by_day, 'top_countries': top_countries,
        'device_split': device_split, 'browser_split': browser_split,
        'top_referrers': top_referrers, 'ab_results': ab,
    })

# ════════════════════════════════════════════════════════════════
# SETTINGS & USER API
# ════════════════════════════════════════════════════════════════

@app.route('/api/user/settings', methods=['POST'])
@login_required
def api_update_settings():
    data = request.get_json() or {}
    display_name = data.get('display_name', '').strip()
    if not display_name:
        return jsonify({'error': 'Display name cannot be empty'}), 400
    if len(display_name) > 100:
        return jsonify({'error': 'Display name too long (max 100 chars)'}), 400
    
    current_user.display_name = display_name
    db.session.commit()
    return jsonify({'message': 'Settings updated successfully', 'display_name': display_name})


# ── Health check ──────────────────────────────────────────────────
@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'redis': REDIS_OK,
                    'timestamp': datetime.utcnow().isoformat()})

# ── Error handlers ────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not found'}), 404
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Internal server error'}), 500
    return render_template('error.html', title='Server Error',
                           msg='Something went wrong. Please try again.'), 500

# ── Init DB + run ─────────────────────────────────────────────────
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=app.config.get('DEBUG', True), host='0.0.0.0', port=port)
