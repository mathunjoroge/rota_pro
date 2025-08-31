from flask import Blueprint, render_template, request, redirect, url_for, Response, abort, make_response
from datetime import date, datetime,timedelta
from collections import defaultdict
from io import BytesIO
from logic.leave_logic import save_leave_logic, get_leaves_on_date, delete_leave_logic, edit_leave_logic
from blueprints.routes import login_required
from models.models import Leave, Rota, OrgDetails,RotaAssignment,Shift
from xhtml2pdf import pisa

import logging

# Setup logging
logging.basicConfig(level=logging.ERROR)

# Blueprints
leave_bp = Blueprint('leave', __name__)
pdf_bp = Blueprint('pdf', __name__)

# Utility function to calculate start and end dates
def calculate_date_range(rotas):
    if not rotas:
        return None, None
    try:
        start_date = min(datetime.strptime(rota.week_range.split(' - ')[0], '%d/%m/%Y') for rota in rotas)
        end_date = max(datetime.strptime(rota.week_range.split(' - ')[1], '%d/%m/%Y') for rota in rotas)
        return start_date, end_date
    except Exception as e:
        logging.error(f"Error calculating date range: {e}")
        return None, None

# Leave routes
@leave_bp.route('/save_leave/<int:member_id>', methods=['POST'])
@login_required
def save_leave(member_id):
    return save_leave_logic(member_id, request.form)

@leave_bp.route('/on_leave')
@login_required
def on_leave():
    leaves = Leave.query.all()
    current_date = date.today()
    return render_template('on_leave.html', leaves=leaves, current_date=current_date)

@leave_bp.route('/delete_leave/<int:leave_id>', methods=['POST'])
@login_required
def delete_leave(leave_id):
    return delete_leave_logic(leave_id)

@leave_bp.route('/edit_leave/<int:leave_id>', methods=['GET', 'POST'])
@login_required
def edit_leave(leave_id):
    if request.method == 'POST':
        return edit_leave_logic(leave_id, request.form)
    leave = Leave.query.get_or_404(leave_id)
    return render_template('edit_leave.html', leave=leave)

# PDF generation routes
@pdf_bp.route('/export_pdf/<int:rota_id>', methods=['GET'])
@login_required
def export_pdf(rota_id):
    """
    Exports the detailed weekly assignments for a specific rota as a PDF.
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

    # Render the HTML template for the PDF.
    html = render_template(
        'pdf_template.html',
        rota_id=rota_id,
        rota_info=rota_info,
        sorted_weekly_data=sorted_weekly_data,
        timedelta=timedelta,
        shift_info=shift_info
    )

    # Generate the PDF.
    pdf_buffer = BytesIO()
    try:
        # Convert HTML to PDF
        pisa_status = pisa.CreatePDF(html, dest=pdf_buffer, encoding='utf-8')
        if pisa_status.err:
            abort(500, f"Failed to generate PDF: {pisa_status.err}")
    except Exception as e:
        abort(500, f"Failed to generate PDF: {str(e)}")

    # Create the response with the PDF.
    pdf_buffer.seek(0)
    response = make_response(pdf_buffer.read())
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=rota_{rota_id}_schedule.pdf'

    # Close the buffer
    pdf_buffer.close()

    return response

@pdf_bp.route('/leave_rota_pdf')
@login_required
def leave_rota_pdf():
    try:
        rotas = Rota.query.all()
        org_details = OrgDetails.query.all()
        leaves = Leave.query.all()  # Fetch leaves data
        start_date, end_date = calculate_date_range(rotas)
        
        # Get current date and format it
        current_date = datetime.now().strftime('%B %d, %Y')  # Format: Month Day, Year
        
        # Render HTML with the current date passed in the context
        html = render_template('leave_rota_pdf.html', leaves=leaves, org_details=org_details, 
                               start_date=start_date, end_date=end_date, current_date=current_date)
        
        pdf = BytesIO()
        pisa_status = pisa.CreatePDF(html, dest=pdf)
        
        if pisa_status.err:
            logging.error("Error generating PDF for leave rota")
            return "Error generating PDF", 500
        
        pdf.seek(0)
        return Response(pdf, mimetype='application/pdf', headers={'Content-Disposition': 'attachment;filename=leave_rota.pdf'})
    except Exception as e:
        logging.error(f"Error in leave_rota_pdf: {e}")
        return "Error generating PDF", 500
