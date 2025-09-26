# utility.py
import requests
from models import User, Card, db
from flask import session, url_for, current_app as app
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
import random
import string

CRYPTO_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "DOGE": "dogecoin"
}

def generate_iban():
    # structure Italian IBAN:
    # IT (2) + CC (2) + CIN (1) + ABI (5) + CAB (5) + Conto (12)
    country_code = 'IT'
    control_digits = ''  # to be calculated
    
    cin = random.choice(string.digits)
    abi = ''.join(random.choice(string.digits) for _ in range(5))
    cab = ''.join(random.choice(string.digits) for _ in range(5))

    account_number = ''.join(random.choice(string.digits) for _ in range(12))

    iban_string = f"{cin}{abi}{cab}{account_number}"

    numeric_string = f"{iban_string}182900"
    
    numeric_string = ''.join([str(int(c, 36)) if c.isalpha() else c for c in numeric_string])
    
    remainder = int(numeric_string) % 97
    checksum = 98 - remainder
    control_digits = f"{checksum:02d}"

    return f"{country_code}{control_digits}{iban_string}"


def send_otp(email):
    otp = str(random.randint(100000, 999999))
    session["otp"] = otp
    # Access the app configuration to get the email credentials
    from flask import current_app as app
    EMAIL_USER = app.config['EMAIL_USER']
    EMAIL_PASS = app.config['EMAIL_PASS']

    msg = MIMEText(f"Il tuo codice OTP è: {otp}", "plain", "utf-8")
    msg["Subject"] = "Codice OTP"
    msg["From"] = EMAIL_USER
    msg["To"] = email

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.sendmail(EMAIL_USER, email, msg.as_string())


def generate_card(user_id):
    # generate unique card number
    while True:
        number = ''.join([str(random.randint(0, 9)) for _ in range(16)])
        existing = Card.query.filter_by(number=number).first()
        if not existing:
            break

    # generate CVV
    cvv = ''.join([str(random.randint(0, 9)) for _ in range(3)])

    # generate expiry date (MM/YY) between today and 5 years
    today = datetime.today()
    expiry_date = today + timedelta(days=5*365)
    expiry = expiry_date.strftime("%m/%y")

    new_card = Card(number=number, cvv=cvv, expiry=expiry, user_id=user_id)
    db.session.add(new_card)
    db.session.commit()

    return new_card

def send_security_alert(email, ip=None, user_agent=None, attempts=0, locked_until=None):
    """
    Invia una mail di notifica di accesso fallito.
    - email: destinatario
    - ip: IP del client (request.remote_addr)
    - user_agent: request.headers.get('User-Agent')
    - attempts: numero tentativi falliti correnti
    - locked_until: datetime se account bloccato (opzionale)
    """
    try:
        time_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        body_lines = [
            f"Ciao,",
            "",
            "Abbiamo rilevato un tentativo di accesso al tuo account.",
            f"Data/ora: {time_str}",
        ]
        if ip:
            body_lines.append(f"IP: {ip}")
        if user_agent:
            body_lines.append(f"User-Agent: {user_agent}")
        body_lines.append(f"Tentativi falliti recenti: {attempts}")
        if locked_until:
            body_lines.append(f"Account bloccato fino a: {locked_until.strftime('%d/%m/%Y %H:%M:%S')}")
        body_lines += [
            "",
            "Sei stato tu? Se sì, ignora questa email.",
            "Se non sei tu, ti consigliamo di resettare la password immediatamente cliccando qui:"
        ]

        # link per reset (puoi usare la route esistente forgot_password)
        reset_url = url_for('routes.forgot_password', _external=True)
        body_lines.append(reset_url)
        body_lines.append("")
        body_lines.append("Se hai bisogno di assistenza, contatta il supporto.")

        body = "\n".join(body_lines)

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = "Sicurezza account: tentativo di accesso rilevato — Sei tu?"
        msg["From"] = app.config.get('EMAIL_USER')
        msg["To"] = email

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(app.config['EMAIL_USER'], app.config['EMAIL_PASS'])
            server.sendmail(app.config['EMAIL_USER'], [email], msg.as_string())
    except Exception as e:
        # non vogliamo far crashare il login per problemi di mail — logga l'errore
        app.logger.exception("Errore invio security alert: %s", e)

def fetch_crypto_price(symbol="bitcoin", vs="usd"):
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={symbol}&vs_currencies={vs}"
    r = requests.get(url).json()
    return r.get(symbol, {}).get(vs, None)

def get_crypto_price(symbol):
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={symbol}&vs_currencies=usd"
    r = requests.get(url).json()
    return r[symbol]["usd"]