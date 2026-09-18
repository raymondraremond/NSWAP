from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
import urllib.parse
import os

# Paystack secret key — set this in Railway environment variables
PAYSTACK_SECRET_KEY = os.environ.get('PAYSTACK_SECRET_KEY', '')

app = Flask(__name__, static_folder='public', static_url_path='')


# ---------------------------------------------------------
# 1. Database Connection & URL Encoding
# ---------------------------------------------------------
# Railway injects DATABASE_URL automatically when a Postgres plugin is attached.
# Falls back to local credentials when running locally.

database_url = os.environ.get('DATABASE_URL')

if database_url:
    # Railway sometimes provides 'postgres://' which SQLAlchemy requires as 'postgresql://'
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql+psycopg2://', 1)
    elif database_url.startswith('postgresql://'):
        database_url = database_url.replace('postgresql://', 'postgresql+psycopg2://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # Local fallback
    raw_password = "$A08138529746a"
    safe_password = urllib.parse.quote_plus(raw_password)
    app.config['SQLALCHEMY_DATABASE_URI'] = f"postgresql+psycopg2://postgres:{safe_password}@localhost:5432/exchange_db"

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False


db = SQLAlchemy(app)

# ---------------------------------------------------------
# 2. Database Schema
# ---------------------------------------------------------
class Category(db.Model):
    __tablename__ = 'categories'
    id = db.Column(db.Integer, primary_key=True)
    category_name = db.Column(db.String(100), nullable=False)

class User(db.Model):
    __tablename__ = 'users'
    identifier = db.Column(db.String(50), primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    sex = db.Column(db.String(10), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)

class CurrencyExchange(db.Model):
    __tablename__ = 'currency_exchanges'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(50), db.ForeignKey('users.identifier'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)
    currency_have = db.Column(db.String(100), nullable=False)
    currency_need = db.Column(db.String(100), nullable=False)

class CryptoExchange(db.Model):
    __tablename__ = 'crypto_exchanges'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(50), db.ForeignKey('users.identifier'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)
    crypto_have = db.Column(db.String(100), nullable=False)
    crypto_need = db.Column(db.String(100), nullable=False)

class GoodsExchange(db.Model):
    __tablename__ = 'goods_exchanges'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(50), db.ForeignKey('users.identifier'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)
    item_have = db.Column(db.String(100), nullable=False)
    item_need = db.Column(db.String(100), nullable=False)

class ServiceExchange(db.Model):
    __tablename__ = 'services_exchanges'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(50), db.ForeignKey('users.identifier'), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=False)
    service_offered = db.Column(db.String(100), nullable=False)
    service_needed = db.Column(db.String(100), nullable=False)

# Track individual user matches to enforce 1 match per user per entry
class ListingMatch(db.Model):
    __tablename__ = 'listing_matches'
    id = db.Column(db.Integer, primary_key=True)
    exchange_type = db.Column(db.String(20), nullable=False)
    entry_id = db.Column(db.Integer, nullable=False)
    user_id = db.Column(db.String(50), db.ForeignKey('users.identifier'), nullable=False)

# Track users who have previously published and deleted a listing in a section
class DeletedListingHistory(db.Model):
    __tablename__ = 'deleted_listing_histories'
    id = db.Column(db.Integer, primary_key=True)
    exchange_type = db.Column(db.String(20), nullable=False)
    user_id = db.Column(db.String(50), db.ForeignKey('users.identifier'), nullable=False)

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
        db.create_all()
        if not Category.query.first():
            category_objects = [Category(id=idx + 1, category_name=name) for idx, name in enumerate(CUSTOM_CATEGORIES)]
            db.session.bulk_save_objects(category_objects)
            db.session.commit()
        print("[OK] POSTGRESQL CONNECTED & SCHEMA VERIFIED.")
    except Exception as e:
        print(f"[ERROR] DATABASE CONNECTION ERROR: {e}")

# ---------------------------------------------------------
# 3. Routes & API Endpoints
# ---------------------------------------------------------
@app.route('/api/categories', methods=['GET'])
def get_categories():
    categories = Category.query.order_by(Category.id.asc()).all()
    return jsonify({'success': True, 'categories': [{'id': c.id, 'name': c.category_name} for c in categories]})

@app.route('/api/users/register', methods=['POST'])
def register_user():
    data = request.json or {}
    identifier = data.get('code')

    if User.query.get(identifier):
        return jsonify({'success': False, 'message': 'Code ID already registered. Use Log In instead.'}), 400

    category = Category.query.get(data.get('category_id'))
    if not category:
        return jsonify({'success': False, 'message': 'Invalid state choice.'}), 400

    new_user = User(
        identifier=identifier,
        name=data.get('name'),
        sex=data.get('sex'),
        phone=data.get('phone'),
        category_id=category.id
    )
    db.session.add(new_user)
    db.session.commit()

    return jsonify({
        'success': True,
        'user': {
            'code': new_user.identifier,
            'name': new_user.name,
            'state_id': category.id,
            'state_name': category.category_name
        }
    })

@app.route('/api/users/login', methods=['POST'])
def login_user():
    data = request.json or {}
    code = data.get('code', '').strip()
    name = data.get('name', '').strip()

    user = User.query.get(code)
    if not user or user.name.lower() != name.lower():
        return jsonify({'success': False, 'message': 'Invalid Code ID or Full Name.'}), 401

    category = Category.query.get(user.category_id)
    return jsonify({
        'success': True,
        'user': {
            'code': user.identifier,
            'name': user.name,
            'state_id': category.id,
            'state_name': category.category_name
        }
    })

@app.route('/api/exchanges/<exchange_type>/<int:category_id>', methods=['GET', 'POST'])
def handle_exchanges(exchange_type, category_id):
    models = {
        'currency': CurrencyExchange,
        'crypto': CryptoExchange,
        'goods': GoodsExchange,
        'services': ServiceExchange
    }
    model = models.get(exchange_type)
    if not model:
        return jsonify({'success': False, 'message': 'Invalid category.'}), 404

    if request.method == 'POST':
        data = request.json or {}
        user_id = data.get('user_id')
        
        user = User.query.get(user_id)
        if not user or user.category_id != category_id:
            return jsonify({'success': False, 'message': 'Unauthorized state access.'}), 403

        # RESTRICTION 1: Check if user has previously completed/deleted a listing in this section
        has_deleted_history = DeletedListingHistory.query.filter_by(
            exchange_type=exchange_type, 
            user_id=user_id
        ).first()
        if has_deleted_history:
            return jsonify({
                'success': False, 
                'message': f'You have already listed and completed an exchange in {exchange_type.capitalize()}. You cannot add another listing in this section.'
            }), 400

        # RESTRICTION 2: Check if user already has an active listing in this section
        existing_listing = model.query.filter_by(user_id=user_id).first()
        if existing_listing:
            return jsonify({
                'success': False, 
                'message': f'You already have an active listing in {exchange_type.capitalize()}.'
            }), 400

        if exchange_type == 'currency':
            entry = CurrencyExchange(user_id=user_id, category_id=category_id, currency_have=data.get('have'), currency_need=data.get('need'))
        elif exchange_type == 'crypto':
            entry = CryptoExchange(user_id=user_id, category_id=category_id, crypto_have=data.get('have'), crypto_need=data.get('need'))
        elif exchange_type == 'goods':
            entry = GoodsExchange(user_id=user_id, category_id=category_id, item_have=data.get('have'), item_need=data.get('need'))
        elif exchange_type == 'services':
            entry = ServiceExchange(user_id=user_id, category_id=category_id, service_offered=data.get('have'), service_needed=data.get('need'))

        db.session.add(entry)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Listing saved.'})

    entries = model.query.filter_by(category_id=category_id).all()
    results = []
    for e in entries:
        matches = ListingMatch.query.filter_by(exchange_type=exchange_type, entry_id=e.id).all()
        matched_user_ids = [m.user_id for m in matches]

        item_data = {
            'id': e.id, 
            'user_id': e.user_id, 
            'match_count': len(matched_user_ids),
            'matched_by_users': matched_user_ids
        }
        
        if exchange_type == 'currency':
            item_data.update({'have': e.currency_have, 'need': e.currency_need})
        elif exchange_type == 'crypto':
            item_data.update({'have': e.crypto_have, 'need': e.crypto_need})
        elif exchange_type == 'goods':
            item_data.update({'have': e.item_have, 'need': e.item_need})
        elif exchange_type == 'services':
            item_data.update({'have': e.service_offered, 'need': e.service_needed})
            
        results.append(item_data)

    return jsonify({'success': True, 'entries': results})

# Register a match on an entry (One match per user per entry)
@app.route('/api/exchanges/<exchange_type>/<int:entry_id>/match', methods=['POST'])
def match_exchange(exchange_type, entry_id):
    data = request.json or {}
    user_id = data.get('user_id')

    if not user_id:
        return jsonify({'success': False, 'message': 'User ID required.'}), 400

    existing_match = ListingMatch.query.filter_by(
        exchange_type=exchange_type,
        entry_id=entry_id,
        user_id=user_id
    ).first()

    if existing_match:
        return jsonify({'success': False, 'message': 'You have already marked this entry as matched.'}), 400

    new_match = ListingMatch(exchange_type=exchange_type, entry_id=entry_id, user_id=user_id)
    db.session.add(new_match)
    db.session.commit()

    return jsonify({'success': True, 'message': 'Marked as matched.'})

# Delete entry ONLY if requester is creator AND at least one user matched it
@app.route('/api/exchanges/<exchange_type>/<int:entry_id>', methods=['DELETE'])
def delete_exchange(exchange_type, entry_id):
    user_id = request.args.get('user_id')
    
    models = {
        'currency': CurrencyExchange,
        'crypto': CryptoExchange,
        'goods': GoodsExchange,
        'services': ServiceExchange
    }
    model = models.get(exchange_type)
    if not model:
        return jsonify({'success': False, 'message': 'Invalid category.'}), 404

    entry = model.query.get(entry_id)
    if not entry:
        return jsonify({'success': False, 'message': 'Listing not found.'}), 404

    if entry.user_id != user_id:
        return jsonify({'success': False, 'message': 'Unauthorized. Only the owner can mark this entry as settled.'}), 403

    # ENFORCE RULE: Can only delete if matched by someone else
    match_count = ListingMatch.query.filter_by(exchange_type=exchange_type, entry_id=entry_id).count()
    if match_count == 0:
        return jsonify({
            'success': False, 
            'message': 'Cannot delete or settle this listing yet. It must be matched by at least one other user first.'
        }), 400

    # Clean up associated matches
    ListingMatch.query.filter_by(exchange_type=exchange_type, entry_id=entry_id).delete()
    
    # Record history to permanently block future listings in this section by this user
    history = DeletedListingHistory(exchange_type=exchange_type, user_id=user_id)
    db.session.add(history)

    # Delete main listing
    db.session.delete(entry)
    db.session.commit()

    return jsonify({'success': True, 'message': 'Listing marked as settled and removed. You can no longer add new listings in this section.'})

# Fetch user details via URL query parameter
@app.route('/api/users/details', methods=['GET'])
def get_user_by_query():
    user_id = request.args.get('id')
    if not user_id:
        return jsonify({'success': False, 'message': 'User ID required.'}), 400

    user = User.query.get(user_id) or User.query.get(urllib.parse.unquote(user_id))
    if not user:
        return jsonify({'success': False, 'message': 'User not found.'}), 404

    category = Category.query.get(user.category_id)
    return jsonify({
        'success': True,
        'user': {
            'code': user.identifier, 
            'name': user.name, 
            'sex': user.sex, 
            'phone': user.phone, 
            'state': category.category_name if category else 'N/A'
        }
    })

# Fetch user details via path parameter
@app.route('/api/users/<path:user_id>', methods=['GET'])
def get_user(user_id):
    decoded_id = urllib.parse.unquote(user_id)
    user = User.query.get(user_id) or User.query.get(decoded_id)
    
    if not user:
        return jsonify({'success': False, 'message': 'User not found.'}), 404
        
    category = Category.query.get(user.category_id)
    return jsonify({
        'success': True,
        'user': {
            'code': user.identifier, 
            'name': user.name, 
            'sex': user.sex, 
            'phone': user.phone, 
            'state': category.category_name if category else 'N/A'
        }
    })

@app.route('/')
def index():
    return send_from_directory('public', 'index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3000, debug=True)