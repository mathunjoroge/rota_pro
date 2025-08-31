import logging
import random
from datetime import date, timedelta
from collections import defaultdict
from tabulate import tabulate
from colorama import init, Fore, Style

# Assuming your models are in 'models.models'
from models.models import db, Rota, Team, Leave, Shift, RotaAssignment

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize colorama
init(autoreset=True)

# --- Configuration Constants ---
# Names of shifts that have special rules.
# The generator will enforce that a member who works a NIGHT_SHIFT_NAME
# must be assigned to a POST_NIGHT_SHIFT_NAME the following week.
NIGHT_SHIFT_NAME = "Night"
POST_NIGHT_SHIFT_NAME = "Night Off"

def generate_unique_rota_id():
    """Generates a unique 10-digit rota ID."""
    while True:
        rota_id = random.randint(10**9, 10**10 - 1)
        if not db.session.query(Rota).filter_by(rota_id=rota_id).first():
            return rota_id

def filter_eligible_members(members, week_start_date, week_end_date):
    """Filters out members on leave for a given week."""
    eligible = []
    for member in members:
        on_leave = db.session.query(Leave).filter(
            Leave.member_id == member.id,
            Leave.start_date <= week_end_date,
            Leave.end_date >= week_start_date
        ).first()
        if not on_leave:
            eligible.append(member)
        else:
            logger.info(f"Member {member.name} is on leave and will be excluded from this week.")
    return eligible

def is_member_exempt(member, shift):
    """Checks if a member is exempt from a specific shift type based on their role."""
    # Admins (is_admin=1) are exempt from all special shifts (assumed to be non-day shifts)
    if member.is_admin == 1 and shift.min_members < 3: # Heuristic for special shifts
        return True
    # Evening exempt (is_admin=2)
    if member.is_admin == 2 and 'evening' in shift.name.lower():
        return True
    # Night exempt (is_admin=3)
    if member.is_admin == 3 and ('night' in shift.name.lower()):
        return True
    return False
    
def _pick_best_candidate(candidates, shift, member_states):
    """
    Picks the best candidate for a shift based on fairness.
    - Prioritizes members who have worked this shift the fewest times.
    - Then prioritizes members who have worked the fewest total special shifts.
    """
    if not candidates:
        return None

    # Sort candidates by fairness criteria
    candidates.sort(key=lambda m: (
        member_states[m.name]['counts'][shift.name],
        member_states[m.name]['total_special_shifts'],
        m.name  # Stable tie-breaker
    ))
    return candidates[0]

def _generate_weekly_rota(rota_id, week_start_date, eligible_members, shifts, member_states, last_week_assignments):
    """
    Generates and saves the rota for a single week.
    This is the core logic engine.
    """
    logger.info(f"--- Generating Rota for Week: {week_start_date.strftime('%Y-%m-%d')} ---")
    
    # Identify default shift (highest max members) and special shifts
    default_shift = max(shifts, key=lambda s: s.max_members)
    special_shifts = sorted([s for s in shifts if s != default_shift], key=lambda s: s.min_members)

    assignments = {shift.name: [] for shift in shifts}
    available_members = set(eligible_members)

    # 1. Handle Mandatory Assignments (e.g., Night Off after Night)
    night_shift_member_name = last_week_assignments.get(NIGHT_SHIFT_NAME)
    if night_shift_member_name:
        post_night_shift = next((s for s in shifts if s.name == POST_NIGHT_SHIFT_NAME), None)
        member = next((m for m in available_members if m.name == night_shift_member_name), None)
        if member and post_night_shift:
            logger.info(f"Assigning {member.name} to {post_night_shift.name} (mandatory post-night shift).")
            assignments[post_night_shift.name].append(member)
            available_members.remove(member)

    # 2. Fill Special Shifts
    for shift in special_shifts:
        while len(assignments[shift.name]) < shift.min_members:
            # Build a list of candidates who are available and not exempt
            candidates = [
                m for m in available_members if not is_member_exempt(m, shift)
            ]
            if not candidates:
                raise ValueError(f"Not enough eligible members to fill the '{shift.name}' shift for week {week_start_date}.")

            # Pick the best candidate based on fairness
            best_candidate = _pick_best_candidate(candidates, shift, member_states)
            if not best_candidate:
                raise ValueError(f"Could not select a candidate for '{shift.name}' shift.")
            
            logger.info(f"Assigning {best_candidate.name} to {shift.name}.")
            assignments[shift.name].append(best_candidate)
            available_members.remove(best_candidate)

    # 3. Assign remaining members to the default shift
    admins = {m for m in available_members if m.is_admin == 1}
    non_admins = available_members - admins
    
    assignments[default_shift.name].extend(list(admins))
    assignments[default_shift.name].extend(list(non_admins))

    # 4. Validate and Save assignments, and update states
    for shift in shifts:
        if len(assignments[shift.name]) < shift.min_members:
            raise ValueError(f"Validation Error: '{shift.name}' has {len(assignments[shift.name])} members, but requires {shift.min_members}.")
        if len(assignments[shift.name]) > shift.max_members:
            logger.warning(f"'{shift.name}' has {len(assignments[shift.name])} members, exceeding the max of {shift.max_members}.")
    
    current_week_assignments = {}
    for shift_name, members in assignments.items():
        shift_obj = next(s for s in shifts if s.name == shift_name)
        is_special = shift_obj != default_shift
        for member in members:
            # Save to DB
            db.session.add(RotaAssignment(
                rota_id=rota_id,
                week_start_date=week_start_date,
                member_id=member.id,
                shift_id=shift_obj.id
            ))
            # Update in-memory state for next week
            member_states[member.name]['counts'][shift_name] += 1
            if is_special:
                 member_states[member.name]['total_special_shifts'] += 1
            # Record assignment for the next iteration's `last_week_assignments`
            if len(members) == 1:
                current_week_assignments[shift_name] = member.name
    
    return current_week_assignments


def display_rota_table(rota_id):
    """Displays the fully generated rota in a colorful, tabulated format."""
    logger.info(f"--- Rota ID: {rota_id} ---")
    
    assignments = db.session.query(RotaAssignment)\
        .filter_by(rota_id=rota_id)\
        .order_by(RotaAssignment.week_start_date).all()
    if not assignments:
        print(f"{Fore.RED}No rota entries found for Rota ID: {rota_id}{Style.RESET_ALL}")
        return

    shifts = db.session.query(Shift).order_by(Shift.start_time).all()
    shift_names = [s.name for s in shifts]

    # --- Color-coded headers ---
    colors = [Fore.YELLOW, Fore.MAGENTA, Fore.CYAN, Fore.LIGHTBLUE_EX, Fore.LIGHTGREEN_EX]
    headers = [f"{Fore.CYAN}📅 Week Range{Style.RESET_ALL}"]
    for i, name in enumerate(shift_names):
        headers.append(f"{colors[i % len(colors)]}{name}{Style.RESET_ALL}")

    # --- Group assignments by week ---
    weekly_data = defaultdict(lambda: {name: [] for name in shift_names})
    for assign in assignments:
        week_start = assign.week_start_date
        weekly_data[week_start][assign.shift.name].append(assign.member.name)
        
    table_data = []
    for week_start, shift_assignments in sorted(weekly_data.items()):
        week_end = week_start + timedelta(days=6)

        # Highlight weekends (optional)
        week_label = f"{Fore.LIGHTWHITE_EX}{week_start.strftime('%d %b')} - {week_end.strftime('%d %b %Y')}{Style.RESET_ALL}"

        row = [week_label]
        for i, name in enumerate(shift_names):
            members = ", ".join(sorted(shift_assignments[name]))
            if not members:
                members = "-"
            row.append(f"{colors[i % len(colors)]}{members}{Style.RESET_ALL}")
        table_data.append(row)
        
    print(tabulate(table_data, headers=headers, tablefmt="fancy_grid", stralign="center"))

def generate_period_rota(start_date, period_weeks, week_duration_days=7):
    """
    Main function to generate a rota for a specified period.
    
    Args:
        start_date (date): The start date of the rota period.
        period_weeks (int): The number of weeks to generate.
        week_duration_days (int): The number of days in a week (usually 7).

    Returns:
        int: The generated rota_id.
    """
    logger.info(f"Starting rota generation for {period_weeks} weeks from {start_date}.")
    
    # 1. Pre-computation and Validation
    all_members = db.session.query(Team).all()
    shifts = db.session.query(Shift).all()
    if not all_members:
        raise ValueError("Cannot generate rota. No members found in the database.")
    if not shifts:
        raise ValueError("Cannot generate rota. No shifts found in the database. Please define shifts first.")
    
    rota_id = generate_unique_rota_id()
    
    # 2. Initialize State
    member_states = {
        m.name: {
            'counts': {s.name: 0 for s in shifts},
            'total_special_shifts': 0
        } for m in all_members
    }
    last_week_assignments = {} # Tracks who was on a single-person shift last week

    # 3. Create the main Rota entry
    end_date = start_date + timedelta(days=(period_weeks * week_duration_days) - 1)
    new_rota = Rota(rota_id=rota_id, start_date=start_date, end_date=end_date)
    db.session.add(new_rota)

    # 4. Generate Rota Week by Week
    for week in range(period_weeks):
        current_date = start_date + timedelta(days=week * week_duration_days)
        week_end_date = current_date + timedelta(days=week_duration_days - 1)
        
        # Filter out members on leave for the current week
        week_eligible_members = filter_eligible_members(all_members, current_date, week_end_date)
        
        if not week_eligible_members:
            logger.error(f"No eligible members for the week starting {current_date}. Stopping generation.")
            break
            
        try:
            current_week_assignments = _generate_weekly_rota(
                rota_id=rota_id,
                week_start_date=current_date,
                eligible_members=week_eligible_members,
                shifts=shifts,
                member_states=member_states,
                last_week_assignments=last_week_assignments
            )
            last_week_assignments = current_week_assignments
        except ValueError as e:
            logger.error(f"A critical error occurred during rota generation for week {current_date}: {e}")
            logger.error("Generation aborted. Rolling back database changes for this rota.")
            db.session.rollback() # Rollback to prevent partial rota
            return None

    # 5. Finalize and Display
    db.session.commit()
    logger.info(f"Successfully generated and saved Rota ID: {rota_id}.")
    display_rota_table(rota_id)
    
    return rota_id