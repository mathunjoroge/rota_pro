import logging
from datetime import datetime,timedelta,date
from collections import defaultdict
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required

# --- Updated Imports ---
# RotaAssignment and Shift are new, ShiftHistory and MemberShiftState are removed.
from models.models import db, Rota, Team, Shift, RotaAssignment
from logic.rota_logic import generate_period_rota, RotaGenerationError
from blueprints.members import requires_level

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

rota_bp = Blueprint('rota', __name__)

@rota_bp.route('/rotas', methods=['GET'])
@login_required
def list_rotas():
    """
    Displays a list of all previously generated rota schedules.
    """
    # The new Rota model has one entry per generated rota, which simplifies this query.
    all_rotas = Rota.query.order_by(Rota.start_date.desc()).all()
    return render_template('rotas_list.html', rotas=all_rotas)

@rota_bp.route('/generate_rota', methods=['GET', 'POST'])
@login_required
def generate_rota():
    """
    Handles the generation of a new rota schedule.
    The GET request shows the form, and POST triggers the generation logic.
    """
    if request.method == 'POST':
        try:
            start_date_str = request.form['start_date']
            # End date calculation can be simplified to just a number of weeks
            period_weeks = int(request.form['period_weeks'])
            
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()

            if period_weeks < 1:
                flash("Period must be at least one week.", 'danger')
                return redirect(url_for('rota.generate_rota'))

            # --- Simplified and More Robust Logic Call ---
            # The logic function now handles all setup, validation, and error logging.
            # It returns the ID on success and None on failure.
            rota_id = generate_period_rota(
                start_date=start_date,
                period_weeks=period_weeks
            )

            if rota_id:
                flash(f"Rota generated successfully with ID {rota_id} for {period_weeks} weeks.", 'success')
                return redirect(url_for('rota.rota_detail', rota_id=rota_id))
            else:
                # The generator function now returns None on failure and logs the specific error.
                flash("Rota generation failed. The configuration may be impossible to satisfy (e.g., not enough staff). Check server logs for details.", 'danger')

        except (ValueError, TypeError):
            # Catches errors from int() or strptime()
            flash("Invalid input. Please check the date and number of weeks.", 'danger')
        except Exception as e:
            # Catch any other unexpected errors
            logger.error(f"An unexpected error occurred in the rota generation route: {str(e)}")
            flash(f"A server error occurred. Please contact an administrator.", 'danger')
            
        return redirect(url_for('rota.generate_rota'))

    # For GET request, suggest a default start date (next Monday)
    today = date.today()
    next_monday = today + timedelta(days=-today.weekday(), weeks=1)
    return render_template('rota.html', default_start_date=next_monday)

@rota_bp.route('/rota/<int:rota_id>', methods=['GET'])
@login_required
def rota_detail(rota_id):
    """
    Displays the detailed weekly assignments for a specific rota.
    """
    # Verify the rota exists.
    rota_info = Rota.query.filter_by(rota_id=rota_id).first()
    if not rota_info:
        abort(404, "Rota with this ID was not found.")

    # Fetch all assignments for this rota.
    assignments = RotaAssignment.query.filter_by(rota_id=rota_id).all()
    
    # Fetch all shifts.
    shifts = Shift.query.all()
    
    # Define the desired shift order by database name.
    desired_shift_names = ['Day', 'Evening', 'Night', 'Night Off']
    
    # Create a mapping of shift names to formatted display names and store shift info.
    shift_info = []
    shift_name_map = {}
    for shift in shifts:
        if shift.name in desired_shift_names:
            if shift.name == 'Night Off':
                display_name = 'Night Off'
            else:
                start = datetime.strptime(str(shift.start_time), '%H:%M:%S').strftime('%I:%M %p').lstrip('0')
                end = datetime.strptime(str(shift.end_time), '%H:%M:%S').strftime('%I:%M %p').lstrip('0')
                display_name = f'{start}–{end}'
            shift_info.append({'name': shift.name, 'display_name': display_name})
            shift_name_map[shift.name] = display_name
    
    # Sort shift_info to match desired_shift_names order.
    shift_info = sorted(shift_info, key=lambda x: desired_shift_names.index(x['name']))
    
    # Extract display names for mapping assignments.
    display_shift_names = [item['display_name'] for item in shift_info]
    
    # Verify all required shifts exist.
    available_shift_names = {s.name for s in shifts}
    for shift_name in desired_shift_names:
        if shift_name not in available_shift_names:
            abort(500, f"Shift '{shift_name}' not found in the database.")

    # Process assignments into a structure the template can easily render:
    # { week_start_date: { display_shift_name: [member_names] } }
    weekly_data = defaultdict(lambda: {name: [] for name in display_shift_names})
    for assign in assignments:
        if assign.shift.name in desired_shift_names:
            display_name = shift_name_map.get(assign.shift.name, assign.shift.name)
            weekly_data[assign.week_start_date][display_name].append(assign.member.name)
    
    # Sort the data by week for display.
    sorted_weekly_data = sorted(weekly_data.items())

    return render_template(
        'rota_detail.html',
        rota_id=rota_id,
        rota_info=rota_info,
        sorted_weekly_data=sorted_weekly_data,
        timedelta=timedelta,
        shift_info=shift_info
    )

@rota_bp.route('/delete_rota/<int:rota_id>', methods=['POST'])
@login_required
@requires_level(1)
def delete_rota(rota_id):
    """
    Deletes a specific rota and all its associated assignments.
    """
    try:
        rota_to_delete = Rota.query.filter_by(rota_id=rota_id).first()
        if rota_to_delete:
            # The 'cascade' option in the model automatically deletes related RotaAssignment records.
            db.session.delete(rota_to_delete)
            db.session.commit()
            flash(f'Rota ID {rota_id} and all its assignments have been deleted.', 'success')
        else:
            flash(f'Rota ID {rota_id} not found.', 'warning')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting rota ID {rota_id}: {str(e)}")
        flash(f"An error occurred while deleting the rota: {str(e)}", 'danger')
        
    return redirect(url_for('rota.list_rotas'))
