from flask_sqlalchemy import SQLAlchemy
from datetime import date
from flask_login import UserMixin
db = SQLAlchemy()

class OrgDetails(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    department = db.Column(db.String(255))


class Leave(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    member_id = db.Column(db.Integer, db.ForeignKey('team.id'), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    member = db.relationship('Team', backref=db.backref('leaves', cascade="all, delete"))

    def days_taken(self):
        """Calculate the number of days taken."""
        if self.start_date and self.end_date:
            return (self.end_date - self.start_date).days + 1
        return 0

    def days_remaining(self):
        """Calculate the number of days remaining from today."""
        if self.end_date:
            remaining_days = (self.end_date - date.today()).days
            return max(remaining_days, 0)  # Ensure it doesn't return negative values
        return 0

    def __repr__(self):
        return f"<Leave {self.id} - Member {self.member_id}: {self.start_date} to {self.end_date}>"

class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    # 0: Standard, 1: Admin (Day shifts only), 2: Evening Exempt, 3: Night Exempt
    is_admin = db.Column(db.Integer, default=0, nullable=False)

    def __repr__(self):
        return f"<Team {self.name}>"

class Shift(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    # The minimum number of members required for this shift to be valid.
    min_members = db.Column(db.Integer, nullable=False, default=1)
    # The maximum number of members that can be assigned to this shift.
    max_members = db.Column(db.Integer, nullable=False, default=10)

    def __repr__(self):
        return f"<Shift {self.name}>"

class Rota(db.Model):
    """Represents a single generated rota schedule, identified by a unique ID."""
    __tablename__ = 'rotas'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    rota_id = db.Column(db.Integer, nullable=False, unique=True)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    # Relationship to the detailed assignments
    assignments = db.relationship('RotaAssignment', backref='rota', cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Rota ID: {self.rota_id}>"

# models/models.py

class RotaAssignment(db.Model):
    """Links a member to a specific shift for a given week within a rota."""
    __tablename__ = 'rota_assignments'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    rota_id = db.Column(db.Integer, db.ForeignKey('rotas.rota_id'), nullable=False)
    week_start_date = db.Column(db.Date, nullable=False)
    
    # --- ADD ondelete="CASCADE" to both ForeignKey lines ---
    member_id = db.Column(db.Integer, db.ForeignKey('team.id', ondelete="CASCADE"), nullable=False)
    shift_id = db.Column(db.Integer, db.ForeignKey('shift.id', ondelete="CASCADE"), nullable=False)
    
    member = db.relationship('Team')
    shift = db.relationship('Shift')

    def __repr__(self):
        return f"<Assignment: {self.member.name} -> {self.shift.name} on {self.week_start_date}>"

class ShiftHistory(db.Model):
    __tablename__ = 'shift_history'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    rota_id = db.Column(db.Integer, nullable=False)
    member_name = db.Column(db.String(255), nullable=False)
    shift_type = db.Column(db.String(50), nullable=False)
    week_range = db.Column(db.String(50), nullable=False)
class MemberShiftState(db.Model):
    __tablename__ = 'member_shift_states'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    rota_id = db.Column(db.Integer, nullable=False)
    member_name = db.Column(db.String(255), nullable=False)
    shift_index = db.Column(db.Integer, nullable=False)    

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(60), nullable=False)
    level = db.Column(db.Integer, default=0)  # New column with default value
    #tempLog
class TemperatureLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=date.today)
    time = db.Column(db.String(5), nullable=False)  # 'AM' or 'PM'
    recorded_temp = db.Column(db.Float, nullable=False)
    acceptable = db.Column(db.Boolean, nullable=False)
    initials = db.Column(db.String(3), nullable=False)
    estimated_room = db.Column(db.Float, nullable=True)  # New column for estimated room temperature 
