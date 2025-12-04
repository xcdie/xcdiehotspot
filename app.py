from flask import Flask, request, redirect, url_for, flash, render_template, jsonify
import os
import requests
import datetime
import base64
import uuid

app = Flask(__name__, template_folder='.')
app.secret_key = os.environ.get('FLASK_SECRET', 'e5f29b7e80b64cc0f6f13a59d90f4c8')


XCDIE_PLANS = [
    # short-term / quick access plans
    {"name": "HIT AND RUN", "price": 5, "duration": "15 Minutes", "tag": "🔥 Quick"},
    {"name": "1 HOUR", "price": 10, "duration": "1 Hour", "tag": ""},
    {"name": "3 HRS", "price": 16, "duration": "3 Hours", "tag": ""},
    {"name": "STREAM 2HRS", "price": 20, "duration": "2 Hours", "tag": "🎬 Stream"},
    {"name": "7 HRS", "price": 25, "duration": "7 Hours", "tag": ""},
    {"name": "12 HRS", "price": 28, "duration": "12 Hours", "tag": ""},

    # Daily Plans
    {"name": "24 HRS LITE", "price": 30, "duration": "1 Day", "tag": ""},
    {"name": "BASIC 20 HRS", "price": 31, "duration": "20 Hours", "tag": "⭐ Value"},
    {"name": "24 HRS BASIC", "price": 35, "duration": "1 Day", "tag": ""},
    {"name": "MAX 24HRS", "price": 40, "duration": "1 Day", "tag": "🚀 Max Speed"},
    {"name": "STREAM 12HRS", "price": 40, "duration": "12 Hours", "tag": "🎬 Stream"},
    {"name": "MAX PLUS 24HRS", "price": 48, "duration": "1 Day", "tag": "🔥 Best Seller"},
    {"name": "24 HRS 2 DEVICES", "price": 50, "duration": "1 Day", "tag": "👥 Shared"},

    # Long-Term / High-Volume Plans
    {"name": "1 WEEK", "price": 200, "duration": "7 Days", "tag": "🗓 Weekly"},
    {"name": "1 MONTH", "price": 550, "duration": "1 Month", "tag": "📅 Monthly"},
]

# M-Pesa / Daraja configuration (set these in environment for production)
MPESA_CONSUMER_KEY = os.environ.get('MPESA_CONSUMER_KEY', 'YOUR_CONSUMER_KEY')
MPESA_CONSUMER_SECRET = os.environ.get('MPESA_CONSUMER_SECRET', 'YOUR_CONSUMER_SECRET')
MPESA_SHORTCODE = os.environ.get('MPESA_SHORTCODE', '174379')      # example sandbox shortcode
MPESA_PASSKEY = os.environ.get('MPESA_PASSKEY', 'YOUR_PASSKEY')
MPESA_ENV = os.environ.get('MPESA_ENV', 'sandbox')  # 'sandbox' or 'production'
# Public callback URL reachable by Safaricom — in sandbox you can use ngrok public URL
MPESA_CALLBACK_URL = os.environ.get('MPESA_CALLBACK_URL', 'https://your-public-callback.example/mpesa/callback')

# Business phone to receive payment (local format: 0799044907 -> international +254799044907)
RECEIVER_PHONE = '+254799044907'

# Optional XCDIE deals url
XCDIE_URL = os.environ.get('XCDIE_URL', 'https://xcdiedeals.com')

# Simple in-memory transaction store (replace with DB in production)
TRANSACTIONS = {}

def mpesa_base_urls():
    if MPESA_ENV == 'production':
        return {
            'oauth': 'https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials',
            'stk_push': 'https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest'
        }
    return {
        'oauth': 'https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials',
        'stk_push': 'https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest'
    }

def get_mpesa_token():
    urls = mpesa_base_urls()
    resp = requests.get(urls['oauth'], auth=(MPESA_CONSUMER_KEY, MPESA_CONSUMER_SECRET), timeout=10)
    resp.raise_for_status()
    return resp.json().get('access_token')

def stk_push(amount, phone, account_reference, description):
    token = get_mpesa_token()
    urls = mpesa_base_urls()
    timestamp = datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')
    password_str = f"{MPESA_SHORTCODE}{MPESA_PASSKEY}{timestamp}"
    password = base64.b64encode(password_str.encode()).decode()

    payload = {
        "BusinessShortCode": MPESA_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(amount),
        "PartyA": phone,                # msisdn paying
        "PartyB": MPESA_SHORTCODE,      # paybill or till
        "PhoneNumber": phone,
        "CallBackURL": MPESA_CALLBACK_URL,
        "AccountReference": account_reference,
        "TransactionDesc": description
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    resp = requests.post(urls['stk_push'], json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.json()

@app.route('/')
def portal():
    support_number = RECEIVER_PHONE
    return render_template('portal.html', plans=XCDIE_PLANS, support_number=support_number)

@app.route('/action', methods=['POST'])
def handle_action():
    action_type = request.form.get('action_type')
    plan_name = request.form.get('plan_name')
    if action_type == 'access':
        flash("Attempting to access network... Please log in first or buy a plan.", 'info')
    elif action_type == 'buy' and plan_name:
        # find plan price
        plan = next((p for p in XCDIE_PLANS if p['name'] == plan_name), None)
        if not plan:
            flash("Selected plan not found.", 'error')
            return redirect(url_for('portal'))
        # create unique transaction id
        tx_id = str(uuid.uuid4())
        TRANSACTIONS[tx_id] = {'plan': plan_name, 'amount': plan['price'], 'status': 'initiated'}
        # initiate STK push to RECEIVER_PHONE on behalf of user (user will enter their own phone on prompt)
        try:
            resp = stk_push(amount=plan['price'],
                            phone=RECEIVER_PHONE,
                            account_reference=plan_name,
                            description=f"Payment for {plan_name} via XCDIE")
            # store response and checkout id if present
            TRANSACTIONS[tx_id]['mpesa_response'] = resp
            TRANSACTIONS[tx_id]['status'] = 'stk_requested'
            flash(f"Payment request sent. Follow the prompt on your phone to complete payment. (Ref: {tx_id})", 'success')
        except Exception as e:
            TRANSACTIONS[tx_id]['status'] = 'error'
            TRANSACTIONS[tx_id]['error'] = str(e)
            flash("Failed to initiate payment. Check server logs and M-Pesa credentials.", 'error')
    return redirect(url_for('portal'))

@app.route('/mpesa/callback', methods=['POST'])
def mpesa_callback():
    # Safaricom will POST JSON here. We record it and update transaction by AccountReference if present.
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "invalid payload"}), 400

    # store callback with server id
    cb_id = str(uuid.uuid4())
    TRANSACTIONS[cb_id] = {'callback': data, 'received_at': datetime.datetime.utcnow().isoformat()}

    # Try to derive account reference or merchant request id to update transaction state
    try:
        # Daraja STK callback structure may include Body.stkCallback
        body = data.get('Body', {})
        stk = body.get('stkCallback', {})
        merchant_req_id = stk.get('MerchantRequestID')
        checkout_req_id = stk.get('CheckoutRequestID')
        result_code = stk.get('ResultCode')
        result_desc = stk.get('ResultDesc')
        # store
        TRANSACTIONS[cb_id].update({
            'merchant_request_id': merchant_req_id,
            'checkout_request_id': checkout_req_id,
            'result_code': result_code,
            'result_desc': result_desc
        })
        # In production you'd correlate merchant_request_id/checkout_request_id to your tx
    except Exception:
        pass

    return jsonify({"status": "ok"}), 200

@app.route('/transactions', methods=['GET'])
def list_transactions():
    # simple view to inspect transaction store (remove or protect in production)
    return jsonify(TRANSACTIONS)

@app.route('/xcdie')
def xcdie_redirect():
    return redirect(XCDIE_URL)

@app.route('/unlimited')
def unlimited_access():
    flash("Accessing Unlimited Network... (Requires admin credentials)", 'warning')
    return redirect(url_for('portal'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
    