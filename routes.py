import json
import random
import string
import smtplib
from datetime import datetime, time, timedelta
from email.mime.text import MIMEText
from flask import jsonify, render_template, request, redirect, session, url_for, flash, Response, Blueprint, current_app as app
import requests
from werkzeug.security import generate_password_hash, check_password_hash
from models import CryptoPriceHistory, CryptoTrade, db, User, Transaction, Card
from prices import PriceError, get_crypto_price, get_fx_rate
from utility import generate_iban, send_otp, generate_card, send_security_alert, fetch_crypto_price,get_crypto_price

from itsdangerous import URLSafeTimedSerializer

PRICE_HISTORY_LIMIT = 50

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
    user = User.query.get(session["user_id"])
    # se il form POST manda un symbol, usalo; altrimenti default
    symbol = request.form.get("symbol", request.args.get("symbol", "bitcoin"))

    if request.method == "POST":
        # Check if this is just a symbol change (no amount provided)
        amount_str = request.form.get("amount", "").strip()
        
        if not amount_str:
            # This is just a symbol change, redirect to GET with the new symbol
            return redirect(url_for("routes.investments", symbol=symbol))
        
        # This is an actual trade submission
        side = request.form["side"]
        try:
            amount = float(amount_str)
        except ValueError:
            flash("Importo non valido!", "error")
            return redirect(url_for("routes.investments", symbol=symbol))
        
        if amount <= 0:
            flash("L'importo deve essere positivo!", "error")
            return redirect(url_for("routes.investments", symbol=symbol))
        
        try:
            price = get_crypto_price(symbol)
        except Exception:
            flash("Errore recupero prezzo", "error")
            return redirect(url_for("routes.investments", symbol=symbol))

        trade = CryptoTrade(
            user_id=user.id,
            symbol=symbol,
            side=side,
            amount=amount,
            price=price
        )
        db.session.add(trade)
        db.session.commit()
        flash("Trade salvato!", "success")
        # redirect con query param per restare sulla stessa moneta
        return redirect(url_for("routes.investments", symbol=symbol))

    # GET: carica trades per la moneta selezionata
    trades = CryptoTrade.query.filter_by(user_id=user.id, symbol=symbol).all()
    trades_json = json.dumps([
        {
            "timestamp": t.timestamp.isoformat(),
            "price": t.price,
            "side": t.side
        } for t in trades
    ])

    return render_template("investments.html",
                           symbol=symbol,
                           trades_json=trades_json)


@bp.route("/api/crypto/<symbol>")
def api_crypto(symbol):
    """
    Recupera il prezzo corrente, lo salva nel DB e restituisce la cronologia.
    """
    # 1. Ottieni il prezzo corrente
    try:
        current_price = get_crypto_price(symbol)
    except Exception:
        return jsonify({"error": "Errore nel recupero del prezzo della crypto"}), 500

    now = datetime.now()

    # 2. Salva il nuovo punto nel database
    new_data_point = CryptoPriceHistory(
        symbol=symbol,
        price=current_price,
        timestamp=now
    )
    db.session.add(new_data_point)
    db.session.commit()

    # 3. Recupera la cronologia (limitata)
    # Recupera gli ultimi N punti per il simbolo corrente
    price_history_objects = CryptoPriceHistory.query.filter_by(symbol=symbol) \
        .order_by(CryptoPriceHistory.timestamp.desc()) \
        .limit(PRICE_HISTORY_LIMIT) \
        .all()
    
    # Inverti l'ordine per avere il punto più vecchio per primo nel grafico
    price_history_objects.reverse() 

    # 4. Formatta e pulisci i dati
    history = [
        {
            "price": p.price,
            "timestamp": p.timestamp.isoformat()
        } for p in price_history_objects
    ]
    
    # (OPZIONALE) Pulizia del DB: Rimuove i dati più vecchi del limite per mantenere il DB snello.
    # Questo è più efficiente se eseguito occasionalmente, non ad ogni chiamata API.
    # Per semplicità qui non lo implementiamo, ma è una best practice.

    # 5. Restituisci la cronologia completa
    return jsonify({
        "history": history
    })

