from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=False, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(128), nullable=False)
    balance = db.Column(db.Float, default=0)
    iban = db.Column(db.String(34), unique=True, nullable=True) 
    pin = db.Column(db.String(6), nullable=True)  # 6-digit PIN for ATM operations
    failed_attempts= db.Column(db.Integer,default=0)
    locked_until= db.Column(db.DateTime, nullable=True)

    def is_locked(self):
        return self.locked_until and self.locked_until > datetime.now()


    def __repr__(self):
        return f'<User {self.name}>'
    
class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False) # positive for deposit, negative for withdrawal
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.now) # default to current time
    type = db.Column(db.String(10), nullable=False) # 'deposit' or 'withdrawal'
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False) # foreign key to User
    user = db.relationship('User', backref=db.backref('transactions', lazy=True)) # relationship to User
    balance_after = db.Column(db.Float, nullable=False)  # balance after this transaction
    category = db.Column(db.String(50))  # es: "transfer_out" or "transfer_in"
    details = db.Column(db.String(120))  # es: "to mario@email.com"


    def __repr__(self):
        return f'<Transaction {self.amount} by User {self.user_id}>'
    


class Card(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(16), unique=True, nullable=False)
    expiry = db.Column(db.String(5), nullable=False) 
    cvv = db.Column(db.String(3), nullable=False)     
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    blocked = db.Column(db.Boolean, default=False)
    user = db.relationship('User', backref=db.backref('cards', lazy=True))

    def __repr__(self):
        return f'<Card {self.number} for User {self.user_id}>'
