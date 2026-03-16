import os, re, json, random, secrets, string, io
from datetime import datetime, timedelta
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

from config import config
from models import db, User, Link, Click, APIKey, BioPage

load_dotenv()

# ── App factory ──────────────────────────────────────────────────
app = Flask(__name__)
env = os.environ.get('FLASK_ENV', 'development')
app.config.from_object(config.get(env, config['default']))

db.init_app(app)
migrate = Migrate(app, db)
CORS(app, resources={r"/api/*": {"origins": app.config.get('FRONTEND_URL', '*')}})

# ── Redis client (Check availability first) ───────────────────────
REDIS_URL = app.config.get('REDIS_URL')
REDIS_OK = False
_redis = None

if REDIS_URL:
    try:
        _redis = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=1)
        _redis.ping()
        REDIS_OK = True
    except Exception:
        print("WARNING: Redis unavailable - using DB-only mode (slower redirects)")
else:
    print("INFO: No REDIS_URL found - using DB-only mode")

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per minute"],
    storage_uri=app.config.get('REDIS_URL') if REDIS_OK else "memory://",
)

login_manager = LoginManager(app)
login_manager.login_view = 'login_page'

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

def log_click(link, variant=None):
    """Async-like click logging — called before redirect."""
    try:
        ip = get_ip()
        country, city = get_geo(ip)
        ua_str = request.headers.get('User-Agent', '')
        device, browser, os_name = parse_ua(ua_str)
        referrer = (request.referrer or '')[:500]
        click = Click(
            link_id=link.id, country=country, city=city,
            device=device, browser=browser, os=os_name,
            referrer=referrer or None, ip_address=ip[:45] if ip else None,
            variant=variant
        )
        db.session.add(click)
        link.click_count += 1
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Click log error: {e}")

# ── API Key decorator ─────────────────────────────────────────────
def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        raw_key = request.headers.get('X-API-Key') or request.args.get('api_key')
        if not raw_key:
            return jsonify({'error': 'API key required'}), 401
        keys = APIKey.query.filter_by(is_active=True).all()
        found = None
        for k in keys:
            if bcrypt.checkpw(raw_key.encode(), k.key_hash.encode()):
                found = k; break
        if not found:
            return jsonify({'error': 'Invalid API key'}), 403
        found.last_used = datetime.utcnow()
        db.session.commit()
        g.api_user_id = found.user_id
        return f(*args, **kwargs)
    return decorated

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

@app.route('/dashboard/bio')
@login_required
def bio_editor():
    return render_template('bio_editor.html', user=current_user)

@app.route('/dashboard/apikeys')
@login_required
def apikeys_page():
    return render_template('api_keys.html', user=current_user)

@app.route('/dashboard/settings')
@login_required
def settings_page():
    return render_template('settings.html', user=current_user)

# ── Public bio page ───────────────────────────────────────────────
@app.route('/b/<username>')
def public_bio(username):
    bio = BioPage.query.filter_by(username=username).first_or_404()
    return render_template('bio.html', bio=bio)

# ════════════════════════════════════════════════════════════════
# REDIRECT ENGINE  /:code
# ════════════════════════════════════════════════════════════════
RESERVED = {'api', 'login', 'register', 'dashboard', 'static', 'health',
            'b', 'admin', 'robots.txt', 'favicon.ico'}

@app.route('/<code>', methods=['GET', 'POST'])
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
        link = type('Link', (), link_data)()  # duck-type for non-DB ops
        link_id = link_data['id']
        # Re-fetch full ORM object for click logging
        link_orm = Link.query.get(link_id)
    else:
        link_orm = Link.query.filter_by(short_code=code).first()
        if not link_orm:
            return render_template('404.html'), 404
        cache_set(f'link:{code}', link_orm.to_dict())

    if link_orm is None:
        return render_template('404.html'), 404

    # 2. Active check
    if not link_orm.is_active:
        return render_template('error.html', title='Link Deactivated',
                               msg='This link has been deactivated.'), 410

    # 3. Expiry check
    if link_orm.expires_at and link_orm.expires_at < datetime.utcnow():
        return render_template('error.html', title='Link Expired',
                               msg='This short link has expired.'), 410

    # 4. Max clicks
    if link_orm.max_clicks and link_orm.click_count >= link_orm.max_clicks:
        return render_template('error.html', title='Link Exhausted',
                               msg='This link has reached its click limit.'), 410

    # 5. Password protection
    if link_orm.password_hash:
        pw = request.args.get('pw') or request.cookies.get(f'pw_{code}')
        if not pw or not link_orm.check_password(pw):
            return render_template('password_gate.html', code=code,
                                   invalid=bool(pw)), 200

    # 6. Parse UA & geo
    ua_str  = request.headers.get('User-Agent', '')
    device, browser, os_name = parse_ua(ua_str)
    ip      = get_ip()
    country, city = get_geo(ip)

    # 7. Determine target URL
    target_url = link_orm.original_url

    # Geo targeting
    if link_orm.geo_rules and country:
        for rule in link_orm.geo_rules:
            if rule.get('country') == country and rule.get('url'):
                target_url = rule['url']; break

    # Device targeting
    if link_orm.device_rules:
        for rule in link_orm.device_rules:
            if rule.get('device', '').lower() == device.lower() and rule.get('url'):
                target_url = rule['url']; break

    # 8. A/B routing
    variant = None
    if link_orm.is_ab_test and link_orm.url_b:
        ratio = link_orm.split_ratio or 50
        variant = 'A' if random.random() * 100 < ratio else 'B'
        if variant == 'B':
            target_url = link_orm.url_b

    # 9. Log click
    log_click(link_orm, variant=variant)

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

@app.route('/api/auth/register', methods=['POST'])
@limiter.limit("20 per 15 minutes")
def api_register():
    data = request.get_json()
    email    = (data.get('email') or '').strip().lower()
    username = (data.get('username') or '').strip()
    password = data.get('password', '')

    if not email or not username or not password:
        return jsonify({'error': 'Email, username, and password required'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    if not re.match(r'^[a-zA-Z0-9_]{3,20}$', username):
        return jsonify({'error': 'Username: 3-20 alphanumeric/underscore chars'}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'Email already registered'}), 409
    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Username already taken'}), 409

    user = User(email=email, username=username)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    login_user(user)
    return jsonify({'user': user.to_dict(), 'message': 'Account created!'}), 201

@app.route('/api/auth/login', methods=['POST'])
@limiter.limit("20 per 15 minutes")
def api_login():
    data = request.get_json()
    email    = (data.get('email') or '').strip().lower()
    password = data.get('password', '')
    if not email or not password:
        return jsonify({'error': 'Email and password required'}), 400
    user = User.query.filter_by(email=email).first()
    if not user or not user.check_password(password):
        return jsonify({'error': 'Invalid email or password'}), 401
    login_user(user, remember=True)
    return jsonify({'user': user.to_dict()})

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

@app.route('/api/links', methods=['POST'])
@login_required
def api_create_link():
    data = request.get_json()
    original_url = (data.get('original_url') or data.get('url') or '').strip()
    if not original_url:
        return jsonify({'error': 'URL is required'}), 400
    try: urlparse(original_url).scheme or (_ for _ in ()).throw(ValueError())
    except: pass  # allow flexible URLs

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

    link = Link(
        user_id=current_user.id, short_code=short_code,
        original_url=original_url, custom_slug=is_custom,
        title=data.get('title'),
        is_ab_test=data.get('is_ab_test', False),
        url_b=data.get('url_b'),
        split_ratio=data.get('split_ratio', 50),
        max_clicks=data.get('max_clicks'),
        geo_rules=data.get('geo_rules'),
        device_rules=data.get('device_rules'),
    )
    if data.get('expires_at'):
        link.expires_at = datetime.fromisoformat(data['expires_at'].replace('Z', '+00:00').replace('+00:00', ''))
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
    fields = ['original_url', 'title', 'is_ab_test', 'url_b', 'split_ratio',
              'max_clicks', 'geo_rules', 'device_rules', 'is_active']
    for f in fields:
        if f in data:
            setattr(link, f, data[f])
    if 'expires_at' in data:
        link.expires_at = datetime.fromisoformat(data['expires_at']) if data['expires_at'] else None
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
    sorted_countries = sorted(cnt.items(), key=lambda x: x[1], reverse=True)[:10]
    top_countries = [{'country': k, 'count': v} for k, v in sorted_countries]

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
    sorted_browsers = sorted(br.items(), key=lambda x: x[1], reverse=True)[:6]
    browser_split = [{'browser': k, 'count': v} for k, v in sorted_browsers]

    # Referrers
    ref = {}
    for c in clicks:
        r = 'Direct'
        if c.referrer:
            try: r = urlparse(c.referrer).netloc.replace('www.', '') or 'Direct'
            except: pass
        ref[r] = ref.get(r, 0) + 1
    sorted_referrers = sorted(ref.items(), key=lambda x: x[1], reverse=True)[:8]
    top_referrers = [{'referrer': k, 'count': v} for k, v in sorted_referrers]

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
# BIO PAGE API
# ════════════════════════════════════════════════════════════════

@app.route('/api/bio', methods=['GET'])
@login_required
def api_get_bio():
    bio = BioPage.query.filter_by(user_id=current_user.id).first()
    return jsonify({'bio': bio.to_dict() if bio else None})

@app.route('/api/bio', methods=['POST'])
@login_required
def api_save_bio():
    data = request.get_json()
    username = (data.get('username') or '').strip()
    display_name = (data.get('display_name') or '').strip()
    if not username or not display_name:
        return jsonify({'error': 'Username and display name required'}), 400
    if not re.match(r'^[a-zA-Z0-9_]{3,20}$', username):
        return jsonify({'error': 'Invalid username format'}), 400
    conflict = BioPage.query.filter_by(username=username).first()
    if conflict and conflict.user_id != current_user.id:
        return jsonify({'error': 'Username already taken'}), 409

    bio = BioPage.query.filter_by(user_id=current_user.id).first()
    if bio is None:
        bio = BioPage(user_id=current_user.id)
        db.session.add(bio)
    bio.username = username
    bio.display_name = display_name
    bio.bio = data.get('bio') or None
    bio.avatar_initial = (data.get('avatar_initial') or display_name[0]).upper()
    bio.tags    = data.get('tags') or []
    bio.links   = data.get('links') or []
    bio.featured = data.get('featured')
    bio.updated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'bio': bio.to_dict()})

@app.route('/api/bio', methods=['DELETE'])
@login_required
def api_delete_bio():
    BioPage.query.filter_by(user_id=current_user.id).delete()
    db.session.commit()
    return jsonify({'message': 'Bio page deleted'})

# ════════════════════════════════════════════════════════════════
# API KEYS API
# ════════════════════════════════════════════════════════════════

@app.route('/api/apikeys', methods=['GET'])
@login_required
def api_list_keys():
    keys = APIKey.query.filter_by(user_id=current_user.id).order_by(APIKey.created_at.desc()).all()
    return jsonify({'keys': [k.to_dict() for k in keys]})

@app.route('/api/apikeys', methods=['POST'])
@login_required
def api_create_key():
    data = request.get_json()
    name = (data.get('name') or '').strip()
    if not name: return jsonify({'error': 'Key name required'}), 400
    count = APIKey.query.filter_by(user_id=current_user.id, is_active=True).count()
    if count >= 5: return jsonify({'error': 'Maximum 5 active API keys allowed'}), 429

    import secrets as sec
    raw_key = f"drizl_{sec.token_hex(16)}"
    key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt()).decode()
    key = APIKey(user_id=current_user.id, name=name,
                 key_hash=key_hash, key_prefix=raw_key[:12])
    db.session.add(key); db.session.commit()
    return jsonify({'key': raw_key, 'key_info': key.to_dict(),
                    'message': 'Save this key — it will not be shown again!'}), 201

@app.route('/api/apikeys/<key_id>', methods=['DELETE'])
@login_required
def api_revoke_key(key_id):
    key = APIKey.query.filter_by(id=key_id, user_id=current_user.id).first_or_404()
    key.is_active = False; db.session.commit()
    return jsonify({'message': 'API key revoked'})

# ════════════════════════════════════════════════════════════════
# PUBLIC REST API v1 (API key auth)
# ════════════════════════════════════════════════════════════════

@app.route('/api/v1/shorten', methods=['POST'])
@limiter.limit("60 per minute")
@require_api_key
def api_v1_shorten():
    data = request.get_json()
    url  = (data.get('url') or '').strip()
    if not url: return jsonify({'error': 'url is required'}), 400
    slug = data.get('slug')
    if slug:
        if not re.match(r'^[a-zA-Z0-9_-]{2,30}$', slug):
            return jsonify({'error': 'Invalid slug'}), 400
        if Link.query.filter_by(short_code=slug).first():
            return jsonify({'error': 'Slug already taken'}), 409
        short_code = slug
    else:
        short_code = make_unique_code()
    link = Link(user_id=g.api_user_id, short_code=short_code, original_url=url,
                custom_slug=bool(slug))
    if data.get('expires_at'):
        link.expires_at = datetime.fromisoformat(data['expires_at'])
    if data.get('max_clicks'):
        link.max_clicks = int(data['max_clicks'])
    db.session.add(link); db.session.commit()
    return jsonify({'short_url': get_short_url(short_code), 'short_code': short_code,
                    'original_url': url, 'created_at': link.created_at.isoformat()}), 201

@app.route('/api/v1/links', methods=['GET'])
@require_api_key
def api_v1_links():
    links = Link.query.filter_by(user_id=g.api_user_id).order_by(Link.created_at.desc()).limit(50).all()
    return jsonify({'links': [{'short_url': get_short_url(l.short_code),
                               'short_code': l.short_code, 'original_url': l.original_url,
                               'clicks': l.click_count} for l in links]})

@app.route('/api/v1/links/<code>', methods=['DELETE'])
@require_api_key
def api_v1_delete(code):
    link = Link.query.filter_by(short_code=code, user_id=g.api_user_id).first_or_404()
    db.session.delete(link); db.session.commit()
    return jsonify({'message': 'Deleted'})

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
