import os
import uuid
from flask import Flask, render_template, request, redirect, url_for, session, flash
import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'super_secret_royal_enfield_key'

# AWS Configuration
AWS_REGION = 'ap-south-1'

# Initialize Boto3 Resources
# 🔐 IAM ROLE INTEGRATION: Boto3 automatically fetches credentials securely 
# from the IAM Role attached to your EC2 instance. NEVER hardcode access keys.
try:
    dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
    sns = boto3.client('sns', region_name=AWS_REGION)
    
    # Define Table References
    users_table = dynamodb.Table('Users')
    bikes_table = dynamodb.Table('Bikes')
    mods_table = dynamodb.Table('Modifications')
    orders_table = dynamodb.Table('Orders')
except Exception as e:
    print(f"Warning: AWS Services not fully configured. Error: {e}")

# Helper: Progress percentage mapping
PROGRESS_MAP = {
    'Requested': 10,
    'Quotation Generated': 25,
    'Advance Paid': 40,
    'In Progress': 70,
    'Completed': 90,
    'Delivered': 100
}

# --- ROUTES ---

@app.route('/')
def index():
    try:
        response = bikes_table.scan()
        bikes = response.get('Items', [])
    except ClientError:
        bikes = [] 
    return render_template('index.html', bikes=bikes)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        
        user_id = str(uuid.uuid4())
        hashed_pw = generate_password_hash(password)
        
        try:
            users_table.put_item(
                Item={
                    'user_id': user_id,
                    'name': name,
                    'email': email,
                    'password': hashed_pw,
                    'role': 'admin' if email == 'admin@royalenfield.com' else 'user'
                }
            )
            flash('Registration successful! Please login.', 'success')
            return redirect(url_for('login'))
        except ClientError as e:
            flash(f"Error registering user: {e}", 'error')
            
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        try:
            response = users_table.scan(FilterExpression=Attr('email').eq(email))
            items = response.get('Items', [])
            
            if items and check_password_hash(items[0]['password'], password):
                user = items[0]
                session['user_id'] = user['user_id']
                session['name'] = user['name']
                session['role'] = user.get('role', 'user')
                
                if session['role'] == 'admin':
                    return redirect(url_for('admin'))
                return redirect(url_for('user_dashboard'))
            else:
                flash('Invalid email or password.', 'error')
        except ClientError as e:
            flash(f"Database error: {e}", 'error')
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out successfully.', 'info')
    return redirect(url_for('index'))

@app.route('/user')
def user_dashboard():
    if 'user_id' not in session or session.get('role') != 'user':
        return redirect(url_for('login'))
        
    try:
        orders_response = orders_table.scan(FilterExpression=Attr('user_id').eq(session['user_id']))
        orders = orders_response.get('Items', [])
        bikes = bikes_table.scan().get('Items', [])
        mods = mods_table.scan().get('Items', [])
    except ClientError:
        orders, bikes, mods = [], [], []
        
    return render_template('user.html', orders=orders, bikes=bikes, mods=mods, progress_map=PROGRESS_MAP)

@app.route('/admin')
def admin():
    if 'user_id' not in session or session.get('role') != 'admin':
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))
        
    try:
        orders = orders_table.scan().get('Items', [])
        bikes = bikes_table.scan().get('Items', [])
    except ClientError:
        orders = []
        bikes = []
        
    return render_template('admin.html', orders=orders, bikes=bikes, progress_map=PROGRESS_MAP)

@app.route('/admin/add_bike', methods=['POST'])
def add_bike():
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    name = request.form.get('name')
    price = request.form.get('price')
    image_url = request.form.get('image_url')
    bike_id = 'b_' + str(uuid.uuid4())[:8]

    try:
        bikes_table.put_item(
            Item={
                'bike_id': bike_id,
                'name': name,
                'price': int(price),
                'image_url': image_url
            }
        )
        flash(f"Bike '{name}' added to showroom successfully!", 'success')
    except ClientError as e:
        flash(f"Error adding bike: {e}", 'error')

    return redirect(url_for('admin'))

@app.route('/buy_bike/<bike_id>', methods=['POST'])
def buy_bike(bike_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    try:
        bike = bikes_table.get_item(Key={'bike_id': bike_id}).get('Item')
        if not bike:
            flash('Bike not found.', 'error')
            return redirect(url_for('user_dashboard'))

        order_id = str(uuid.uuid4())
        orders_table.put_item(
            Item={
                'order_id': order_id,
                'user_id': session['user_id'],
                'bike_id': bike_id,
                'modifications': [],
                'notes': 'Direct Showroom Purchase',
                'status': 'Quotation Generated',
                'total_price': int(bike['price']),
                'advance_paid': False,
                'full_paid': False
            }
        )
        flash('Purchase initiated! Please pay the advance to secure your bike.', 'success')
        return redirect(url_for('quotation', order_id=order_id))
    except ClientError as e:
        flash(f"Error processing purchase: {e}", 'error')
        return redirect(url_for('user_dashboard'))

@app.route('/order', methods=['POST'])
def place_order():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    bike_id = request.form['bike_id']
    mod_ids = request.form.getlist('mods') 
    notes = request.form.get('notes', '')
    
    order_id = str(uuid.uuid4())
    
    try:
        orders_table.put_item(
            Item={
                'order_id': order_id,
                'user_id': session['user_id'],
                'bike_id': bike_id,
                'modifications': mod_ids,
                'notes': notes,
                'status': 'Requested',
                'total_price': 0,
                'advance_paid': False,
                'full_paid': False
            }
        )
        
        # Uncomment and configure topic ARN in production for SNS alerts
        # sns.publish(
        #     TopicArn='arn:aws:sns:us-east-1:123456789012:AdminNotifications',
        #     Message=f"New modification request {order_id} received from user {session['name']}."
        # )
        
        flash('Modification request submitted successfully!', 'success')
    except ClientError as e:
        flash(f"Failed to submit order: {e}", 'error')
        
    return redirect(url_for('user_dashboard'))

@app.route('/admin/update_order/<order_id>', methods=['POST'])
def update_order(order_id):
    if 'user_id' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
        
    new_status = request.form.get('status')
    total_price = request.form.get('total_price')
    
    update_expr = "set #st = :s"
    expr_attrs = {'#st': 'status'}
    expr_vals = {':s': new_status}
    
    if total_price and float(total_price) > 0:
        update_expr += ", total_price = :p"
        expr_vals[':p'] = int(total_price)
        
    try:
        orders_table.update_item(
            Key={'order_id': order_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_attrs,
            ExpressionAttributeValues=expr_vals
        )
        flash(f'Order {order_id} updated successfully.', 'success')
    except ClientError as e:
        flash(f"Update failed: {e}", 'error')
        
    return redirect(url_for('admin'))

@app.route('/quotation/<order_id>')
def quotation(order_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    try:
        response = orders_table.get_item(Key={'order_id': order_id})
        order = response.get('Item')
        if not order:
            flash('Order not found.', 'error')
            return redirect(url_for('user_dashboard'))
            
        bike = bikes_table.get_item(Key={'bike_id': order['bike_id']}).get('Item', {})
        
        mod_details = []
        for mod_id in order.get('modifications', []):
            mod = mods_table.get_item(Key={'mod_id': mod_id}).get('Item', {})
            if mod:
                mod_details.append(mod)
                
    except ClientError as e:
        flash("Error retrieving quotation details.", 'error')
        return redirect(url_for('user_dashboard'))
        
    return render_template('quotation.html', order=order, bike=bike, mods=mod_details)

@app.route('/payment/<order_id>/<payment_type>', methods=['GET', 'POST'])
def payment(order_id, payment_type):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    try:
        order = orders_table.get_item(Key={'order_id': order_id}).get('Item')
    except ClientError:
        flash("Order error.", 'error')
        return redirect(url_for('user_dashboard'))
        
    amount = int(order.get('total_price', 0)) / 2 
    
    if request.method == 'POST':
        try:
            if payment_type == 'advance':
                orders_table.update_item(
                    Key={'order_id': order_id},
                    UpdateExpression="set advance_paid = :val, #st = :s",
                    ExpressionAttributeNames={'#st': 'status'},
                    ExpressionAttributeValues={':val': True, ':s': 'Advance Paid'}
                )
            elif payment_type == 'full':
                orders_table.update_item(
                    Key={'order_id': order_id},
                    UpdateExpression="set full_paid = :val, #st = :s",
                    ExpressionAttributeNames={'#st': 'status'},
                    ExpressionAttributeValues={':val': True, ':s': 'Delivered'}
                )
            flash(f'{payment_type.capitalize()} payment successful!', 'success')
        except ClientError as e:
            flash("Payment update failed.", 'error')
            
        return redirect(url_for('user_dashboard'))
        
    return render_template('payment.html', order=order, amount=amount, payment_type=payment_type)

def initialize_db():
    """Helper to mock initial data if tables are empty/new."""
    try:
        if not bikes_table.scan()['Items']:
            bikes_table.put_item(Item={'bike_id': 'b1', 'name': 'Classic 350', 'price': 190000, 'image_url': 'https://images.unsplash.com/photo-1558981403-c5f9899a28bc?w=800&auto=format&fit=crop'})
            bikes_table.put_item(Item={'bike_id': 'b2', 'name': 'Continental GT 650', 'price': 310000, 'image_url': 'https://images.unsplash.com/photo-1623055403061-893bd6527b13?w=800&auto=format&fit=crop'})
        
        if not mods_table.scan()['Items']:
            mods_table.put_item(Item={'mod_id': 'm1', 'name': 'Custom Matte Paint', 'base_price': 15000})
            mods_table.put_item(Item={'mod_id': 'm2', 'name': 'Performance Exhaust', 'base_price': 8000})
            mods_table.put_item(Item={'mod_id': 'm3', 'name': 'Touring Seats', 'base_price': 4500})
            mods_table.put_item(Item={'mod_id': 'm4', 'name': 'Alloy Wheels', 'base_price': 12000})
    except Exception as e:
        pass

if __name__ == '__main__':
    # initialize_db() # Uncomment this locally on first run to populate DB
    app.run(debug=True, port=5000)
