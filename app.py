from flask import Flask, request, jsonify, send_from_directory, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from functools import wraps
import urllib.parse
import os
import re
import hmac
import hashlib
import requests as http_requests
from datetime import datetime, timezone
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
# ---------------------------------------------------------
# Paystack credentials — NEVER hardcode; set in Railway env
# ---------------------------------------------------------
PAYSTACK_SECRET_KEY = os.environ.get('PAYSTACK_SECRET_KEY', '')

app = Flask(__name__, static_folder='public', static_url_path='')

# ---------------------------------------------------------
# Session & Security Settings
# ---------------------------------------------------------
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'default_nswap_super_secret_key_change_me')
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# In production, require HTTPS cookies
if os.environ.get('RAILWAY_ENVIRONMENT'):
    app.config['SESSION_COOKIE_SECURE'] = True

# ---------------------------------------------------------
# Rate Limiting
# ---------------------------------------------------------
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    return response

# ---------------------------------------------------------
# 1. Database Connection
# ---------------------------------------------------------
database_url = os.environ.get('DATABASE_URL')

if database_url:
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql+psycopg2://', 1)
    elif database_url.startswith('postgresql://'):
        database_url = database_url.replace('postgresql://', 'postgresql+psycopg2://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    raw_password = "$A08138529746a"
    safe_password = urllib.parse.quote_plus(raw_password)
    app.config['SQLALCHEMY_DATABASE_URI'] = (
        f"postgresql+psycopg2://postgres:{safe_password}@localhost:5432/exchange_db"
    )

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)


# ---------------------------------------------------------
# 2. Database Models
# ---------------------------------------------------------

class Category(db.Model):
    __tablename__ = 'categories'
    id = db.Column(db.Integer, primary_key=True)
    category_name = db.Column(db.String(100), nullable=False)


class User(db.Model):
    __tablename__ = 'users'
    identifier  = db.Column(db.String(50), primary_key=True)
    name        = db.Column(db.String(100), nullable=False)
    sex         = db.Column(db.String(10),  nullable=False)
    phone       = db.Column(db.String(20),  nullable=False)
    email       = db.Column(db.String(200), nullable=True)   # nullable for existing rows
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)


class CurrencyExchange(db.Model):
    __tablename__ = 'currency_exchanges'
    id            = db.Column(db.Integer, primary_key=True)
    user_id       = db.Column(db.String(50), db.ForeignKey('users.identifier'), nullable=False)
    category_id   = db.Column(db.Integer,    db.ForeignKey('categories.id'),    nullable=False)
    currency_have = db.Column(db.String(100), nullable=False)
    currency_need = db.Column(db.String(100), nullable=False)


class CryptoExchange(db.Model):
    __tablename__ = 'crypto_exchanges'
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.String(50), db.ForeignKey('users.identifier'), nullable=False)
    category_id = db.Column(db.Integer,    db.ForeignKey('categories.id'),    nullable=False)
    crypto_have = db.Column(db.String(100), nullable=False)
    crypto_need = db.Column(db.String(100), nullable=False)


class GoodsExchange(db.Model):
    __tablename__ = 'goods_exchanges'
    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.String(50), db.ForeignKey('users.identifier'), nullable=False)
    category_id = db.Column(db.Integer,    db.ForeignKey('categories.id'),    nullable=False)
    item_have   = db.Column(db.String(100), nullable=False)
    item_need   = db.Column(db.String(100), nullable=False)


class ServiceExchange(db.Model):
    __tablename__ = 'services_exchanges'
    id               = db.Column(db.Integer, primary_key=True)
    user_id          = db.Column(db.String(50), db.ForeignKey('users.identifier'), nullable=False)
    category_id      = db.Column(db.Integer,    db.ForeignKey('categories.id'),    nullable=False)
    service_offered  = db.Column(db.String(100), nullable=False)
    service_needed   = db.Column(db.String(100), nullable=False)


# Track individual user matches (1 match per user per entry)
class ListingMatch(db.Model):
    __tablename__ = 'listing_matches'
    id            = db.Column(db.Integer, primary_key=True)
    exchange_type = db.Column(db.String(20), nullable=False)
    entry_id      = db.Column(db.Integer,    nullable=False)
    user_id       = db.Column(db.String(50), db.ForeignKey('users.identifier'), nullable=False)


# Prevents re-listing after a listing is settled
class DeletedListingHistory(db.Model):
    __tablename__ = 'deleted_listing_histories'
    id            = db.Column(db.Integer, primary_key=True)
    exchange_type = db.Column(db.String(20), nullable=False)
    user_id       = db.Column(db.String(50), db.ForeignKey('users.identifier'), nullable=False)


class ContactUnlock(db.Model):
    """
    Records a ₦500 contact-unlock payment for a specific listing.
    A user must have status='success' for a given (requester_id, exchange_type, entry_id)
    before the backend will return that listing owner's phone number.
    """
    __tablename__        = 'contact_unlocks'
    id                   = db.Column(db.Integer, primary_key=True)
    requester_id         = db.Column(db.String(50),  db.ForeignKey('users.identifier'), nullable=False)
    exchange_type        = db.Column(db.String(20),  nullable=False)
    entry_id             = db.Column(db.Integer,     nullable=False)
    paystack_reference   = db.Column(db.String(200), unique=True, nullable=False)
    status               = db.Column(db.String(20),  nullable=False, default='pending')
    # status values: pending | success | failed
    amount               = db.Column(db.Integer,     nullable=False, default=50000)  # kobo
    created_at           = db.Column(db.DateTime,    nullable=False,
                                     default=lambda: datetime.now(timezone.utc))
    paid_at              = db.Column(db.DateTime,    nullable=True)


# ---------------------------------------------------------
# 3. Startup: create tables & safe migrations
# ---------------------------------------------------------
CUSTOM_CATEGORIES = [
    "Abia", "Adamawa", "Akwa Ibom", "Anambra", "Bauchi", "Bayelsa", "Benue", "Borno",
    "Cross River", "Delta", "Ebonyi", "Edo", "Ekiti", "Enugu", "Federal Capital Territory (FCT)",
    "Gombe", "Imo", "Jigawa", "Kaduna", "Kano", "Katsina", "Kebbi", "Kogi", "Kwara",
    "Lagos", "Nasarawa", "Niger", "Ogun", "Ondo", "Osun", "Oyo", "Plateau", "Rivers",
    "Sokoto", "Taraba", "Yobe", "Zamfara"
]

with app.app_context():
    try:
        db.session.execute(text('SELECT 1'))
        # Creates new tables (e.g. contact_unlocks). Safe no-op for existing tables.
        db.create_all()

        # Add email column to existing users table if it doesn't exist yet
        try:
            db.session.execute(
                text('ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(200)')
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

        # Seed states if first run
        if not Category.query.first():
            objects = [
                Category(id=idx + 1, category_name=name)
                for idx, name in enumerate(CUSTOM_CATEGORIES)
            ]
            db.session.bulk_save_objects(objects)
            db.session.commit()

        print("[OK] POSTGRESQL CONNECTED & SCHEMA VERIFIED.")
    except Exception as e:
        print(f"[ERROR] DATABASE CONNECTION ERROR: {e}")


# ---------------------------------------------------------
# 4. Helpers
# ---------------------------------------------------------
EXCHANGE_MODELS = {
    'currency': CurrencyExchange,
    'crypto':   CryptoExchange,
    'goods':    GoodsExchange,
    'services': ServiceExchange,
}


def _contact_json(unlock):
    """
    Build the contact response for a verified unlock.
    Only called after status='success' is confirmed.
    """
    model = EXCHANGE_MODELS.get(unlock.exchange_type)
    if not model:
        return jsonify({'success': False, 'message': 'Invalid exchange type.'}), 400

    entry = model.query.get(unlock.entry_id)
    if not entry:
        return jsonify({'success': False, 'message': 'Listing no longer exists.'}), 404

    owner = User.query.get(entry.user_id)
    if not owner:
        return jsonify({'success': False, 'message': 'Listing owner not found.'}), 404

    cat = Category.query.get(owner.category_id)
    return jsonify({
        'success': True,
        'contact': {
            'code':  owner.identifier,
            'name':  owner.name,
            'sex':   owner.sex,
            'phone': owner.phone,
            'email': owner.email or 'No email provided',
            'state': cat.category_name if cat else 'N/A',
        }
    })


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'success': False, 'message': 'Authentication required. Please log in.'}), 401
        return f(*args, **kwargs)
    return decorated_function

# ---------------------------------------------------------
# 5. Routes — Auth
# ---------------------------------------------------------

@app.route('/api/categories', methods=['GET'])
def get_categories():
    cats = Category.query.order_by(Category.id.asc()).all()
    return jsonify({
        'success': True,
        'categories': [{'id': c.id, 'name': c.category_name} for c in cats]
    })


@app.route('/api/users/register', methods=['POST'])
@limiter.limit("3 per minute")
def register_user():
    """
    FREE registration — no payment required.
    Saves state code, name, sex, phone, email, deployment state.
    """
    data = request.json or {}
    identifier = data.get('code', '').strip().upper()

    if not identifier:
        return jsonify({'success': False, 'message': 'State code is required.'}), 400

    if User.query.get(identifier):
        return jsonify({'success': False,
                        'message': 'That state code is used already. Please log in instead.'}), 400

    category = Category.query.get(data.get('category_id'))
    if not category:
        return jsonify({'success': False, 'message': 'Invalid state choice.'}), 400

    new_user = User(
        identifier  = identifier,
        name        = data.get('name', '').strip(),
        sex         = data.get('sex', ''),
        phone       = data.get('phone', '').strip(),
        email       = data.get('email', '').strip() or None,
        category_id = category.id,
    )
    db.session.add(new_user)
    db.session.commit()

    session['user_id'] = new_user.identifier
    session.permanent = True
    
    return jsonify({
        'success': True,
        'user': {
            'code':       new_user.identifier,
            'name':       new_user.name,
            'email':      new_user.email or '',
            'state_id':   category.id,
            'state_name': category.category_name,
        }
    })


@app.route('/api/users/login', methods=['POST'])
@limiter.limit("5 per minute")
def login_user():
    data = request.json or {}
    code = data.get('code', '').strip().upper()
    name = data.get('name', '').strip()

    user = User.query.get(code)
    if not user or user.name.lower() != name.lower():
        return jsonify({'success': False, 'message': 'Invalid Code ID or Full Name.'}), 401

    category = Category.query.get(user.category_id)
    
    session['user_id'] = user.identifier
    session.permanent = True
    
    return jsonify({
        'success': True,
        'user': {
            'code':       user.identifier,
            'name':       user.name,
            'email':      user.email or '',
            'state_id':   category.id,
            'state_name': category.category_name,
        }
    })

@app.route('/api/users/logout', methods=['POST'])
def logout_user():
    session.pop('user_id', None)
    return jsonify({'success': True, 'message': 'Logged out successfully.'})


# ---------------------------------------------------------
# 6. Routes — Listings (unchanged logic, kept intact)
# ---------------------------------------------------------

@app.route('/api/exchanges/<exchange_type>/<int:category_id>', methods=['GET', 'POST'])
def handle_exchanges(exchange_type, category_id):
    model = EXCHANGE_MODELS.get(exchange_type)
    if not model:
        return jsonify({'success': False, 'message': 'Invalid category.'}), 404

    if request.method == 'POST':
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'success': False, 'message': 'Authentication required. Please log in.'}), 401

        data    = request.json or {}
        
        user = User.query.get(user_id)
        if not user or user.category_id != category_id:
            return jsonify({'success': False, 'message': 'Unauthorized state access.'}), 403

        # Block: user already completed + deleted a listing in this section
        if DeletedListingHistory.query.filter_by(
                exchange_type=exchange_type, user_id=user_id).first():
            return jsonify({
                'success': False,
                'message': (f'You have already listed and completed an exchange in '
                            f'{exchange_type.capitalize()}. You cannot add another listing '
                            f'in this section.')
            }), 400

        # Block: user has an active listing in this section
        if model.query.filter_by(user_id=user_id).first():
            return jsonify({
                'success': False,
                'message': f'You already have an active listing in {exchange_type.capitalize()}.'
            }), 400

        if exchange_type == 'currency':
            entry = CurrencyExchange(
                user_id=user_id, category_id=category_id,
                currency_have=data.get('have'), currency_need=data.get('need'))
        elif exchange_type == 'crypto':
            entry = CryptoExchange(
                user_id=user_id, category_id=category_id,
                crypto_have=data.get('have'), crypto_need=data.get('need'))
        elif exchange_type == 'goods':
            entry = GoodsExchange(
                user_id=user_id, category_id=category_id,
                item_have=data.get('have'), item_need=data.get('need'))
        elif exchange_type == 'services':
            entry = ServiceExchange(
                user_id=user_id, category_id=category_id,
                service_offered=data.get('have'), service_needed=data.get('need'))

        db.session.add(entry)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Listing saved.'})

    # GET — return listings for this state (no phone in response)
    entries = model.query.filter_by(category_id=category_id).all()
    results = []
    for e in entries:
        matches         = ListingMatch.query.filter_by(exchange_type=exchange_type, entry_id=e.id).all()
        matched_uids    = [m.user_id for m in matches]
        item            = {
            'id':               e.id,
            'user_id':          e.user_id,
            'match_count':      len(matched_uids),
            'matched_by_users': matched_uids,
        }
        if exchange_type == 'currency':
            item.update({'have': e.currency_have, 'need': e.currency_need})
        elif exchange_type == 'crypto':
            item.update({'have': e.crypto_have,   'need': e.crypto_need})
        elif exchange_type == 'goods':
            item.update({'have': e.item_have,      'need': e.item_need})
        elif exchange_type == 'services':
            item.update({'have': e.service_offered,'need': e.service_needed})
        results.append(item)

    return jsonify({'success': True, 'entries': results})


@app.route('/api/exchanges/<exchange_type>/<int:entry_id>/match', methods=['POST'])
@login_required
def match_exchange(exchange_type, entry_id):
    user_id = session.get('user_id')

    if not user_id:
        return jsonify({'success': False, 'message': 'User ID required.'}), 400

    if ListingMatch.query.filter_by(
            exchange_type=exchange_type, entry_id=entry_id, user_id=user_id).first():
        return jsonify({'success': False,
                        'message': 'You have already marked this entry as matched.'}), 400

    db.session.add(ListingMatch(exchange_type=exchange_type, entry_id=entry_id, user_id=user_id))
    db.session.commit()
    return jsonify({'success': True, 'message': 'Marked as matched.'})


@app.route('/api/exchanges/<exchange_type>/<int:entry_id>', methods=['DELETE'])
@login_required
def delete_exchange(exchange_type, entry_id):
    user_id = session.get('user_id')
    model   = EXCHANGE_MODELS.get(exchange_type)

    if not model:
        return jsonify({'success': False, 'message': 'Invalid category.'}), 404

    entry = model.query.get(entry_id)
    if not entry:
        return jsonify({'success': False, 'message': 'Listing not found.'}), 404

    if entry.user_id != user_id:
        return jsonify({'success': False,
                        'message': 'Unauthorized. Only the owner can mark this entry as settled.'}), 403

    match_count = ListingMatch.query.filter_by(
        exchange_type=exchange_type, entry_id=entry_id).count()
    if match_count == 0:
        return jsonify({
            'success': False,
            'message': ('Cannot delete or settle this listing yet. '
                        'It must be matched by at least one other user first.')
        }), 400

    ListingMatch.query.filter_by(exchange_type=exchange_type, entry_id=entry_id).delete()
    db.session.add(DeletedListingHistory(exchange_type=exchange_type, user_id=user_id))
    db.session.delete(entry)
    db.session.commit()

    return jsonify({
        'success': True,
        'message': ('Listing marked as settled and removed. '
                    'You can no longer add new listings in this section.')
    })


# ---------------------------------------------------------
# 7. Routes — Public User Info (phone NEVER returned here)
# ---------------------------------------------------------

@app.route('/api/users/details', methods=['GET'])
def get_user_by_query():
    """Public profile — no phone number exposed."""
    user_id = request.args.get('id')
    if not user_id:
        return jsonify({'success': False, 'message': 'User ID required.'}), 400

    user = User.query.get(user_id) or User.query.get(urllib.parse.unquote(user_id))
    if not user:
        return jsonify({'success': False, 'message': 'User not found.'}), 404

    cat = Category.query.get(user.category_id)
    return jsonify({
        'success': True,
        'user': {
            'code':  user.identifier,
            'name':  user.name,
            'sex':   user.sex,
            'state': cat.category_name if cat else 'N/A',
            # phone intentionally omitted
        }
    })


@app.route('/api/users/<path:user_id>', methods=['GET'])
def get_user(user_id):
    """Public profile — no phone number exposed."""
    decoded = urllib.parse.unquote(user_id)
    user    = User.query.get(user_id) or User.query.get(decoded)

    if not user:
        return jsonify({'success': False, 'message': 'User not found.'}), 404

    cat = Category.query.get(user.category_id)
    return jsonify({
        'success': True,
        'user': {
            'code':  user.identifier,
            'name':  user.name,
            'sex':   user.sex,
            'state': cat.category_name if cat else 'N/A',
            # phone intentionally omitted
        }
    })


# ---------------------------------------------------------
# 8. Routes — Contact Unlock / Payment
# ---------------------------------------------------------

@app.route('/api/payment/initialize', methods=['POST'])
@login_required
def initialize_payment():
    """
    Initialize a ₦500 Paystack transaction to unlock a specific listing's contact.

    If the user has already paid for this listing → returns contact directly.
    Otherwise  → creates a Paystack transaction and returns the reference.

    IMPORTANT: secret key used server-side only; never sent to the browser.
    """
    data          = request.json or {}
    requester_id  = session.get('user_id')
    exchange_type = data.get('exchange_type', '').strip()
    entry_id      = data.get('entry_id')
    email         = data.get('email', '').strip()

    if not all([requester_id, exchange_type, entry_id]):
        return jsonify({'success': False, 'message': 'Missing required fields.'}), 400

    requester = User.query.get(requester_id)
    if not requester:
        return jsonify({'success': False, 'message': 'User not found.'}), 404

    model = EXCHANGE_MODELS.get(exchange_type)
    if not model:
        return jsonify({'success': False, 'message': 'Invalid exchange type.'}), 400

    entry = model.query.get(entry_id)
    if not entry:
        return jsonify({'success': False, 'message': 'Listing not found.'}), 404

    if entry.user_id == requester_id:
        return jsonify({'success': False,
                        'message': 'You cannot unlock your own listing.'}), 400

    # ── Already unlocked? Return contact immediately (no new charge) ──────────
    existing = ContactUnlock.query.filter_by(
        requester_id  = requester_id,
        exchange_type = exchange_type,
        entry_id      = entry_id,
        status        = 'success',
    ).first()
    if existing:
        return _contact_json(existing)

    # ── Need email for Paystack ───────────────────────────────────────────────
    if not email:
        email = requester.email or ''
    if not email:
        return jsonify({
            'success':    False,
            'need_email': True,
            'message':    'Email address required for payment processing.',
        }), 400

    # Save email to user record if not stored yet (helps future unlocks)
    if not requester.email:
        requester.email = email
        db.session.commit()

    if not PAYSTACK_SECRET_KEY:
        return jsonify({'success': False,
                        'message': 'Payment system not configured. Contact support.'}), 500

    # ── Create Paystack transaction via backend ───────────────────────────────
    ts        = int(datetime.now(timezone.utc).timestamp() * 1000)
    # Paystack only allows alphanumeric + hyphens in references.
    # NYSC state codes contain '/' so we strip all non-alphanumeric chars first.
    safe_id   = re.sub(r'[^A-Za-z0-9]', '', requester_id)
    reference = f'NSWAP-{safe_id}-{exchange_type}-{entry_id}-{ts}'

    try:
        resp = http_requests.post(
            'https://api.paystack.co/transaction/initialize',
            headers={
                'Authorization': f'Bearer {PAYSTACK_SECRET_KEY}',
                'Content-Type':  'application/json',
            },
            json={
                'email':     email,
                'amount':    50000,      # ₦500 in kobo
                'reference': reference,
                'currency':  'NGN',
                'metadata':  {
                    'requester_id':  requester_id,
                    'exchange_type': exchange_type,
                    'entry_id':      entry_id,
                    'custom_fields': [
                        {'display_name': 'Requester',     'variable_name': 'requester_id',  'value': requester_id},
                        {'display_name': 'Exchange Type', 'variable_name': 'exchange_type', 'value': exchange_type},
                        {'display_name': 'Entry ID',      'variable_name': 'entry_id',      'value': str(entry_id)},
                    ],
                },
            },
            timeout=10,
        )
        ps = resp.json()
    except Exception as exc:
        return jsonify({'success': False, 'message': f'Payment service error: {exc}'}), 503

    if not ps.get('status'):
        return jsonify({'success': False,
                        'message': ps.get('message', 'Payment initialization failed.')}), 400

    # ── Store pending record ──────────────────────────────────────────────────
    unlock = ContactUnlock(
        requester_id       = requester_id,
        exchange_type      = exchange_type,
        entry_id           = entry_id,
        paystack_reference = reference,
        status             = 'pending',
        amount             = 50000,
    )
    db.session.add(unlock)
    db.session.commit()

    return jsonify({
        'success':         True,
        'already_unlocked': False,
        'reference':       reference,
    })


@app.route('/api/payment/verify', methods=['POST'])
@login_required
def verify_payment():
    """
    Verify a Paystack payment server-side and grant contact unlock if successful.
    Frontend must call this after Paystack callback fires.
    Backend re-verifies directly with Paystack — never trusts the frontend.
    """
    data         = request.json or {}
    reference    = data.get('reference', '').strip()
    requester_id = session.get('user_id')

    if not reference or not requester_id:
        return jsonify({'success': False,
                        'message': 'reference and user_id are required.'}), 400

    unlock = ContactUnlock.query.filter_by(
        paystack_reference = reference,
        requester_id       = requester_id,
    ).first()

    if not unlock:
        return jsonify({'success': False, 'message': 'Payment record not found.'}), 404

    # Already verified (idempotent)
    if unlock.status == 'success':
        return _contact_json(unlock)

    if not PAYSTACK_SECRET_KEY:
        return jsonify({'success': False,
                        'message': 'Payment system not configured.'}), 500

    # ── Ask Paystack to confirm ───────────────────────────────────────────────
    try:
        safe_ref = urllib.parse.quote(reference, safe='')
        resp     = http_requests.get(
            f'https://api.paystack.co/transaction/verify/{safe_ref}',
            headers={'Authorization': f'Bearer {PAYSTACK_SECRET_KEY}'},
            timeout=10,
        )
        ps = resp.json()
    except Exception as exc:
        return jsonify({'success': False,
                        'message': f'Verification service error: {exc}'}), 503

    if not ps.get('status'):
        return jsonify({'success': False, 'message': 'Verification call failed.'}), 400

    txn = ps.get('data', {})

    if txn.get('status') != 'success':
        unlock.status = 'failed'
        db.session.commit()
        return jsonify({'success': False,
                        'message': f'Payment not successful (status: {txn.get("status")}).'}), 400

    if txn.get('amount', 0) < 50000:
        unlock.status = 'failed'
        db.session.commit()
        return jsonify({'success': False, 'message': 'Payment amount insufficient.'}), 400

    # ── Grant unlock ──────────────────────────────────────────────────────────
    unlock.status  = 'success'
    unlock.paid_at = datetime.now(timezone.utc)
    db.session.commit()

    return _contact_json(unlock)


@app.route('/api/payment/webhook', methods=['POST'])
def paystack_webhook():
    """
    Paystack webhook endpoint for async charge.success events.
    Verifies HMAC-SHA512 signature before processing.
    Idempotent: safe to receive the same event multiple times.
    """
    body      = request.get_data()
    signature = request.headers.get('x-paystack-signature', '')

    # Verify signature
    if PAYSTACK_SECRET_KEY:
        expected = hmac.new(
            PAYSTACK_SECRET_KEY.encode('utf-8'),
            body,
            hashlib.sha512,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return jsonify({'status': 'invalid signature'}), 400

    event = request.json or {}
    if event.get('event') == 'charge.success':
        txn       = event.get('data', {})
        reference = txn.get('reference', '')
        unlock    = ContactUnlock.query.filter_by(paystack_reference=reference).first()
        if unlock and unlock.status == 'pending':
            if txn.get('status') == 'success' and txn.get('amount', 0) >= 50000:
                unlock.status  = 'success'
                unlock.paid_at = datetime.now(timezone.utc)
                db.session.commit()

    return jsonify({'status': 'ok'}), 200


@app.route('/api/contact/<exchange_type>/<int:entry_id>', methods=['GET'])
@login_required
def get_contact_direct(exchange_type, entry_id):
    """
    Protected contact endpoint.
    Returns phone only if requester has a verified (status=success) ContactUnlock
    for this specific listing. All other cases → 403.
    """
    requester_id = session.get('user_id')
    if not requester_id:
        return jsonify({
            'success': False,
            'locked':  True,
            'message': 'Authentication required.',
        }), 401

    unlock = ContactUnlock.query.filter_by(
        requester_id  = requester_id,
        exchange_type = exchange_type,
        entry_id      = entry_id,
        status        = 'success',
    ).first()

    if not unlock:
        return jsonify({
            'success': False,
            'locked':  True,
            'message': 'Contact locked. Complete ₦500 payment to unlock.',
        }), 403

    return _contact_json(unlock)


# ---------------------------------------------------------
# 9. Static / Root
# ---------------------------------------------------------

@app.route('/')
def index():
    return send_from_directory('public', 'index.html')


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3000, debug=True)