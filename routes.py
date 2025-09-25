import random
import string
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from flask import render_template, request, redirect, session, url_for, flash, Response, Blueprint, current_app as app
import requests
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Transaction, Card
from prices import PriceError, get_crypto_price, get_fx_rate
from utility import generate_iban, send_otp, generate_card, send_security_alert

from itsdangerous import URLSafeTimedSerializer

def get_serializer():
    secret_key = app.config.get('SECRET_KEY', 'default-secret-key')
    return URLSafeTimedSerializer(secret_key)


bp = Blueprint('routes', __name__)

def init_app(app):
    """Initializes the routes Blueprint with the Flask app instance."""
    app.register_blueprint(bp)


# -----------------------------
# Routes
# -----------------------------

@bp.route("/")
def index():
    return render_template("index.html")

@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        password = request.form["password"]
        
        existing_user = User.query.filter((User.email == email)).first()
        if existing_user:
            flash("Email già esistente!")
            return redirect(url_for("routes.register"))
        
        hashed_pw = generate_password_hash(password)
        generated_iban = generate_iban() 

        new_user = User(name=name, email=email, password=hashed_pw, balance=0, iban=generated_iban)
        db.session.add(new_user)
        db.session.commit()

        session["user_id"] = new_user.id

        generate_card(new_user.id) 

        flash("Registrazione avvenuta con successo! Imposta il tuo PIN.")
        return redirect(url_for("routes.set_pin"))
    return render_template("register.html")

@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        user = User.query.filter_by(email=email).first()

        if not email or not password:
            flash("Inserisci sia email che password.", "error")
            return redirect(url_for("routes.login"))

        if user.is_locked():
            unlock_time = user.locked_until.strftime("%H:%M:%S")
            flash(f"Account bloccato fino alle {unlock_time}.")
            return render_template("login.html")
        

        if user and check_password_hash(user.password, password):
            user.failed_attempts=0
            db.session.commit()
            session["temp_user_id"] = user.id
            send_otp(user.email)
            return redirect(url_for("routes.verify_otp"))
        
        #wrong pw
        user.failed_attempts+=1

        client_ip = request.remote_addr
        client_ua = request.headers.get('User-Agent')
        # Invia mail al primo tentativo fallito (configurabile)
         
        if user.failed_attempts == 1:
            send_security_alert(user.email, ip=client_ip, user_agent=client_ua, attempts=user.failed_attempts)

        if user.failed_attempts >= 3:
            user.locked_until = datetime.now() + timedelta(minutes=5)  # lock 5 min
            db.session.commit()
            # Invia mail anche al momento del lock con l'informazione del blocco
            send_security_alert(user.email, ip=client_ip, user_agent=client_ua, attempts=user.failed_attempts, locked_until=user.locked_until)
            flash("Troppi tentativi falliti. Account bloccato per 5 minuti.", "error")
        else:
            db.session.commit()
            flash(f"Credenziali non valide! Tentativi rimanenti: {3 - user.failed_attempts}", "error")
        
    return render_template("login.html")

@bp.route("/logout")
def logout():
    session.clear()
    flash("Logout effettuato con successo!")
    return redirect(url_for("routes.index"))

@bp.route("/dashboard")
def dashboard():
    user_id = session.get("user_id")
    user = User.query.get_or_404(user_id)

    if not user.pin:
        flash("Devi impostare un PIN prima di accedere al conto.")
        return redirect(url_for("routes.set_pin"))
    
    transactions = Transaction.query.filter_by(user_id=user_id).order_by(Transaction.timestamp.desc()).all()
    user_card = Card.query.filter_by(user_id=user.id).first()
    return render_template("dashboard.html", user=user, transactions=transactions, user_card=user_card)

@bp.route("/transaction", methods=["GET", "POST"])
def transaction():
    if "user_id" not in session:
        return redirect(url_for("routes.login"))

    user = User.query.get_or_404(session["user_id"])

    card = Card.query.filter_by(user_id=user.id).first()
    if card and card.blocked:
        flash("Operazione non consentita: la tua carta è bloccata.")
        return redirect(url_for("routes.dashboard"))

    if not user.pin:
        flash("Devi impostare un PIN prima di accedere al conto.")
        return redirect(url_for("routes.set_pin"))

    if request.method == "GET":
        t_type = request.args.get("type", "deposit")
        return render_template("transaction.html", user=user, t_type=t_type)

    t_type = request.form.get("type", "deposit")
    entered_pin = request.form.get("pin", "")
    try:
        amount = float(request.form.get("amount", 0))
    except ValueError:
        flash("Importo non valido!")
        return redirect(url_for("routes.dashboard", type=t_type))

    if amount <= 0:
        flash("L'importo deve essere positivo!")
        return redirect(url_for("routes.dashboard", type=t_type))

    if t_type == "withdraw" and amount > user.balance:
        flash("Fondi insufficienti per il prelievo!")
        return redirect(url_for("routes.dashboard", type="withdraw"))

    if not check_password_hash(user.pin, entered_pin):
        flash("PIN errato!")
        return redirect(url_for("routes.transaction", type=t_type))

    if t_type == "deposit":
        user.balance += amount
        signed_amount = amount
    elif t_type == "withdraw":
        user.balance -= amount
        signed_amount = -amount
    else:
        flash("Tipo di operazione non valido!")
        return redirect(url_for("routes.dashboard"))

    new_transaction = Transaction(
        amount=signed_amount,
        timestamp=datetime.now(),
        type=t_type,
        user_id=user.id,
        balance_after=user.balance
    )
    db.session.add(new_transaction)
    db.session.commit()

    flash(f"{'Deposito' if t_type=='deposit' else 'Prelievo'} di {amount:.2f} € effettuato con successo!")
    return redirect(url_for("routes.dashboard"))

@bp.route("/transactions")
def transactions():
    if "user_id" not in session:
        return redirect(url_for("routes.login"))
    user_id = session["user_id"]
    user = User.query.get_or_404(user_id)
    transactions = Transaction.query.filter_by(user_id=user_id).order_by(Transaction.timestamp.desc()).all()
    return render_template("transactions.html", user=user, transactions=transactions)

@bp.route("/transfer", methods=["GET", "POST"])
def transfer():
    if "user_id" not in session:
        return redirect(url_for("routes.login"))

    sender = User.query.get_or_404(session["user_id"])

    if not sender.pin:
        flash("Imposta il PIN prima di fare un trasferimento.")
        return redirect(url_for("routes.set_pin"))

    if request.method == "POST":
        recipient_iban = request.form.get("iban")
        try:
            amount = float(request.form.get("amount", 0))
        except ValueError:
            flash("Importo non valido!")
            return redirect(url_for("routes.transfer"))

        if not recipient_iban:
            flash("IBAN del destinatario richiesto!")
            return redirect(url_for("routes.transfer"))

        if amount <= 0:
            flash("L'importo deve essere positivo!")
            return redirect(url_for("routes.transfer"))

        if amount > sender.balance:
            flash("Saldo insufficiente!")
            return redirect(url_for("routes.transfer"))
        
        if recipient_iban == sender.iban:
            flash("Non puoi trasferire a te stesso!")
            return redirect(url_for("routes.transfer"))
        
        pin = request.form.get("pin", "")
        if not check_password_hash(sender.pin, pin):
            flash("PIN errato!")
            return redirect(url_for("routes.transfer"))

        recipient = User.query.filter_by(iban=recipient_iban).first()

        sender.balance -= amount

        # sender
        db.session.add(Transaction(
            amount=-amount,
            type="transfer",
            category="trasferimento IBAN in uscita",
            user_id=sender.id,
            balance_after=sender.balance,
            details=f"a {recipient.name if recipient else 'IBAN esterno'} ({recipient_iban})"
        ))

        # recipient
        if recipient:
            recipient.balance += amount
            db.session.add(Transaction(
                amount=amount,
                type="transfer",
                category="trasferimento IBAN in entrata",
                user_id=recipient.id,
                balance_after=recipient.balance,
                details=f"da {sender.name} ({sender.iban or 'IBAN non disp.'})"
            ))
            flash(f"Trasferiti {amount:.2f} € a {recipient.name}!")
        else:
            flash(f"Trasferimento di {amount:.2f} € verso l'IBAN esterno {recipient_iban} eseguito con successo!")

        db.session.commit()
        return redirect(url_for("routes.dashboard"))

    return render_template("transfer.html", user=sender)

@bp.route("/set_pin", methods=["GET", "POST"])
def set_pin():
    if "user_id" not in session:
        return redirect(url_for("routes.login"))

    user = User.query.get_or_404(session["user_id"])

    if request.method == "POST":
        pin = request.form.get("pin")
        if pin:
            user.pin = generate_password_hash(pin)
            db.session.commit()
            flash("PIN impostato con successo!")
            return redirect(url_for("routes.dashboard"))
        else:
            flash("Inserisci un PIN valido.")

    return render_template("set_pin.html", user=user)

@bp.route("/verify_otp", methods=["GET", "POST"])
def verify_otp():
    if request.method == "POST":
        otp = request.form.get("otp")
        if otp == session.get("otp"):
            session.pop("otp")
            session["user_id"] = session.get("temp_user_id")
            session.pop("temp_user_id")
            flash("Login completato con 2FA!")
            return redirect(url_for("routes.dashboard"))
        else:
            flash("Codice OTP errato.")
    return render_template("verify_otp.html")

@bp.route("/block_card/<int:card_id>", methods=["POST"])
def block_card(card_id):
    if "user_id" not in session:
        return redirect(url_for("routes.login"))

    card = Card.query.get_or_404(card_id)

    if card.user_id != session["user_id"]:
        flash("Accesso non autorizzato.")
        return redirect(url_for("routes.dashboard"))

    card.blocked = True
    db.session.commit()
    flash("Carta bloccata con successo.")
    return redirect(url_for("routes.show_card", card_id=card.id))

@bp.route("/unblock_card/<int:card_id>", methods=["POST"])
def unblock_card(card_id):
    if "user_id" not in session:
        return redirect(url_for("routes.login"))

    card = Card.query.get_or_404(card_id)

    if card.user_id != session["user_id"]:
        flash("Accesso non autorizzato.")
        return redirect(url_for("routes.dashboard"))

    card.blocked = False
    db.session.commit()
    flash("Carta sbloccata con successo.")
    return redirect(url_for("routes.show_card", card_id=card.id))

@bp.route("/show_card/<int:card_id>", methods=["GET", "POST"])
def show_card(card_id):
    if "user_id" not in session:
        return redirect(url_for("routes.login"))

    card = Card.query.get_or_404(card_id)
    user = User.query.get(card.user_id)

    if card.user_id != session["user_id"]:
        flash("Accesso non autorizzato.")
        return redirect(url_for("routes.dashboard"))

    if request.method == "POST":
        entered_pin = request.form.get("pin")
        if not entered_pin or not check_password_hash(user.pin, entered_pin):
            flash("PIN errato!")
            return render_template("show_card.html", card=card, user=user, show_cvv=False)
        
        return render_template("show_card.html", card=card, user=user, show_cvv=True)

    return render_template("show_card.html", card=card, user=user, show_cvv=False)

@bp.route("/cards")
def show_user_cards():
    if "user_id" not in session:
        return redirect(url_for("routes.login"))
    user = User.query.get_or_404(session["user_id"])
    first_card = user.cards[0] if user.cards else None
    if not first_card:
        flash("Nessuna carta trovata.")
        return redirect(url_for("routes.dashboard"))
    return redirect(url_for("routes.show_card", card_id=first_card.id))

@bp.route("/forgot_password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email")
        user = User.query.filter_by(email=email).first()
        if user:
            # Generate a secure, time-limited token
            s = get_serializer()
            token = s.dumps(user.email, salt='reset-password')
            
            # Send email with the token
            reset_url = url_for('routes.reset_password', token=token, _external=True)
            msg = MIMEText(f"Per resettare la tua password, clicca sul seguente link: {reset_url}. Il link scade tra 1 ora.", "plain", "utf-8")
            msg["Subject"] = "Richiesta di reset password"
            msg["From"] = app.config['EMAIL_USER']
            msg["To"] = email
            
            try:
                with smtplib.SMTP("smtp.gmail.com", 587) as server:
                    server.starttls()
                    server.login(app.config['EMAIL_USER'], app.config['EMAIL_PASS'])
                    server.sendmail(app.config['EMAIL_USER'], email, msg.as_string())
                flash("Una email con le istruzioni per il reset della password è stata inviata.", "success")
            except Exception as e:
                flash(f"Errore nell'invio dell'email: {e}", "error")
        else:
            flash("Email non trovata.", "error")
        return redirect(url_for('routes.login'))
    return render_template("forgot_password.html")

@bp.route("/reset_password/<token>", methods=["GET", "POST"])
def reset_password(token):
    s = get_serializer()
    try:
        email = s.loads(token, salt='reset-password', max_age=3600)  # Token valid for 1 hour
    except:
        flash("Il link di reset è scaduto o non valido.", "error")
        return redirect(url_for('routes.login'))

    user = User.query.filter_by(email=email).first()
    if not user:
        flash("Utente non trovato.", "error")
        return redirect(url_for('routes.login'))

    if request.method == "POST":
        password = request.form.get("password")
        confirm_password = request.form.get("confirm_password")
        
        if password != confirm_password:
            flash("Le password non corrispondono.", "error")
            return redirect(url_for('routes.reset_password', token=token))
        
        user.password = generate_password_hash(password)
        db.session.commit()
        flash("La tua password è stata resettata con successo. Ora puoi effettuare il login.", "success")
        return redirect(url_for('routes.login'))
    
    return render_template("reset_password.html", token=token)

@bp.route("/settings")
def settings():
    if "user_id" not in session:
        flash("Devi accedere per entrare nelle impostazioni.", "error")
        return redirect(url_for("routes.login"))
    return render_template("settings.html")

@bp.route("/settings/set_new_password",methods=["GET", "POST"])
def set_new_password():
    if "user_id" not in session:
        flash("Devi accedere per entrare nelle impostazioni.", "error")
        return redirect(url_for("routes.login"))
    user = User.query.get(session["user_id"])

    if request.method == "POST":
        old_password = request.form.get("old_password")
        new_password = request.form.get("new_password")
        confirm_password = request.form.get("confirm_password")

        if not check_password_hash(user.password,old_password):
            flash("password errata.", "error")
            return redirect(url_for("routes.set_new_password"))
        
        if not new_password or new_password!=confirm_password:
            flash("le password non coincidono.","error")
            return redirect(url_for("routes.set_new_password"))
        
        if len(new_password)< 8:
            flash("La nuova password deve essere lunga almeno 8 caratteri.","error")
            return redirect(url_for("routes.set_new_password"))
        
        user.password= generate_password_hash(new_password)
        db.session.commit()

        flash("password aggiornata con successo!")
        return redirect(url_for("routes.settings"))
    
    return render_template("set_new_password.html")



@bp.route("/investments", methods=["GET", "POST"])
def investments():
    active_tab = request.form.get("tab", "stocks")  
    stock_data, historical_data = None, None
    forex_data, crypto_data = None, None
    api_key = app.config['ALPHA_VANTAGE_API_KEY']

    try:
        if active_tab == "stocks" and request.method == "POST":
            symbol = request.form.get("symbol", "AAPL").upper()
            url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={api_key}"
            r = requests.get(url)
            js = r.json()
            stock_data = js.get("Global Quote", {})
            # storico prezzi (ultimi 30 giorni)
            url_hist = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol={symbol}&apikey={api_key}"
            r = requests.get(url_hist)
            js_hist = r.json()
            historical_data = js_hist.get("Time Series (Daily)", {})

        elif active_tab == "forex" and request.method == "POST":
            base = request.form.get("base", "USD").upper()
            quote = request.form.get("quote", "EUR").upper()
            rate, asof = get_fx_rate(base, quote)
            forex_data = {"base": base, "quote": quote, "rate": rate, "asof": asof}

        elif active_tab == "crypto" and request.method == "POST":
            coin = request.form.get("coin", "bitcoin").lower()
            vs = request.form.get("vs", "usd").lower()
            price = get_crypto_price(coin, vs)
            crypto_data = {"coin": coin, "vs": vs, "price": price}

    except PriceError as e:
        flash(str(e), "error")
    except Exception as e:
        flash(f"Errore API: {e}", "error")

    return render_template("investments.html",
                           active_tab=active_tab,
                           stock_data=stock_data,
                           historical_data=historical_data,
                           forex_data=forex_data,
                           crypto_data=crypto_data)


#Simulazione Acquisto bitcoin
@bp.route("/crypto", methods=["GET", "POST"])
def crypto():
    if "user_id" not in session:
        flash("Devi accedere per visualizzare la sezione crypto.", "error")
        return redirect(url_for("routes.login"))

    user = User.query.get(session["user_id"])
    symbol = request.form.get("symbol", "bitcoin")
    days = int(request.form.get("days", 30))
    action = request.form.get("action")
    quantity = request.form.get("quantity")

    url = f"https://api.coingecko.com/api/v3/coins/{symbol}/market_chart"
    params = {"vs_currency": "usd", "days": days}

    price_data = []
    current_price = 0
    try:
        r = requests.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        price_data = data.get("prices", [])
        if price_data:
            current_price = price_data[-1][1]
    except Exception as e:
        flash(f"Errore API CoinGecko: {e}", "error")

    if request.method == "POST" and action and quantity:
        try:
            quantity = float(quantity)
            total_cost = quantity * current_price

            if action == "Compra":
                if total_cost > user.balance:
                    flash("Saldo insufficiente per acquistare.", "error")
                else:
                    user.balance -= total_cost
                    db.session.commit()
                    flash(f"Hai acquistato {quantity} {symbol} per {total_cost:.2f} USD.", "success")
            elif action == "Vendi":
                user.balance += total_cost
                db.session.commit()
                flash(f"Hai venduto {quantity} {symbol} per {total_cost:.2f} USD.", "success")
        except ValueError:
            flash("Quantità non valida.", "error")

    return render_template("crypto.html", symbol=symbol, price_data=price_data, user=user)


