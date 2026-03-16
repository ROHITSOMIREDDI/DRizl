from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
import bcrypt
import uuid

db = SQLAlchemy()

def gen_uuid():
    return str(uuid.uuid4())


class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id           = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    email        = db.Column(db.String(255), unique=True, nullable=False)
    username     = db.Column(db.String(50), unique=True, nullable=False)
    password_hash= db.Column(db.String(255), nullable=False)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    links        = db.relationship('Link', back_populates='user', cascade='all, delete-orphan')
    api_keys     = db.relationship('APIKey', back_populates='user', cascade='all, delete-orphan')
    bio_page     = db.relationship('BioPage', back_populates='user', uselist=False, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    def check_password(self, password):
        return bcrypt.checkpw(password.encode(), self.password_hash.encode())

    def to_dict(self):
        return {'id': self.id, 'email': self.email, 'username': self.username, 'created_at': self.created_at.isoformat()}


class Link(db.Model):
    __tablename__ = 'links'
    id            = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    user_id       = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    short_code    = db.Column(db.String(50), unique=True, nullable=False, index=True)
    original_url  = db.Column(db.Text, nullable=False)
    custom_slug   = db.Column(db.Boolean, default=False)
    title         = db.Column(db.String(255), nullable=True)

    # A/B testing
    is_ab_test    = db.Column(db.Boolean, default=False)
    url_b         = db.Column(db.Text, nullable=True)
    split_ratio   = db.Column(db.Integer, default=50)  # % to variant A

    # Link controls
    password_hash = db.Column(db.String(255), nullable=True)
    expires_at    = db.Column(db.DateTime, nullable=True)
    max_clicks    = db.Column(db.Integer, nullable=True)
    click_count   = db.Column(db.Integer, default=0)

    # Targeting (stored as JSON text)
    geo_rules     = db.Column(db.JSON, nullable=True)
    device_rules  = db.Column(db.JSON, nullable=True)

    is_active     = db.Column(db.Boolean, default=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user          = db.relationship('User', back_populates='links')
    clicks        = db.relationship('Click', back_populates='link', cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    def check_password(self, password):
        if not self.password_hash:
            return True
        return bcrypt.checkpw(password.encode(), self.password_hash.encode())

    @property
    def status(self):
        if not self.is_active:
            return 'inactive'
        if self.expires_at and self.expires_at < datetime.utcnow():
            return 'expired'
        if self.max_clicks and self.click_count >= self.max_clicks:
            return 'expired'
        if self.is_ab_test:
            return 'ab'
        if self.password_hash:
            return 'protected'
        return 'active'

    def to_dict(self):
        return {
            'id': self.id, 'short_code': self.short_code,
            'original_url': self.original_url, 'title': self.title,
            'custom_slug': self.custom_slug, 'is_ab_test': self.is_ab_test,
            'url_b': self.url_b, 'split_ratio': self.split_ratio,
            'expires_at': self.expires_at.isoformat() if self.expires_at else None,
            'max_clicks': self.max_clicks, 'click_count': self.click_count,
            'geo_rules': self.geo_rules, 'device_rules': self.device_rules,
            'is_active': self.is_active, 'status': self.status,
            'created_at': self.created_at.isoformat(),
        }


class Click(db.Model):
    __tablename__ = 'clicks'
    id         = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    link_id    = db.Column(db.String(36), db.ForeignKey('links.id', ondelete='CASCADE'), nullable=False, index=True)
    country    = db.Column(db.String(10), nullable=True)
    city       = db.Column(db.String(100), nullable=True)
    device     = db.Column(db.String(20), nullable=True)   # mobile|desktop|tablet
    browser    = db.Column(db.String(50), nullable=True)
    os         = db.Column(db.String(50), nullable=True)
    referrer   = db.Column(db.String(500), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    variant    = db.Column(db.String(1), nullable=True)    # A or B
    clicked_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    link = db.relationship('Link', back_populates='clicks')


class APIKey(db.Model):
    __tablename__ = 'api_keys'
    id          = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    user_id     = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    name        = db.Column(db.String(100), nullable=False)
    key_hash    = db.Column(db.String(255), unique=True, nullable=False, index=True)
    key_prefix  = db.Column(db.String(20), nullable=False)
    is_active   = db.Column(db.Boolean, default=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    last_used   = db.Column(db.DateTime, nullable=True)

    user = db.relationship('User', back_populates='api_keys')

    def check_key(self, raw_key):
        return bcrypt.checkpw(raw_key.encode(), self.key_hash.encode())

    def to_dict(self):
        return {'id': self.id, 'name': self.name, 'key_prefix': self.key_prefix,
                'is_active': self.is_active, 'created_at': self.created_at.isoformat(),
                'last_used': self.last_used.isoformat() if self.last_used else None}


class BioPage(db.Model):
    __tablename__ = 'bio_pages'
    id             = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    user_id        = db.Column(db.String(36), db.ForeignKey('users.id', ondelete='CASCADE'), unique=True, nullable=False)
    username       = db.Column(db.String(50), unique=True, nullable=False, index=True)
    display_name   = db.Column(db.String(100), nullable=False)
    bio            = db.Column(db.Text, nullable=True)
    avatar_initial = db.Column(db.String(2), nullable=False, default='D')
    tags           = db.Column(db.JSON, nullable=True)      # list of strings
    links          = db.Column(db.JSON, nullable=False, default=list)
    featured       = db.Column(db.Integer, nullable=True)   # index of featured link
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at     = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', back_populates='bio_page')

    def to_dict(self):
        return {
            'id': self.id, 'username': self.username,
            'display_name': self.display_name, 'bio': self.bio,
            'avatar_initial': self.avatar_initial, 'tags': self.tags or [],
            'links': self.links or [], 'featured': self.featured,
        }
