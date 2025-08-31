# rota_logic.py

import logging
import random
from datetime import date, timedelta
from collections import defaultdict
from typing import List, Dict, Set, Optional, Any

from tabulate import tabulate
from colorama import init, Fore, Style

# Assuming your models are in 'models.models'
from models.models import db, Rota, Team, Leave, Shift, RotaAssignment

# --- Configuration ---
# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize colorama
init(autoreset=True)

# --- Custom Exception ---
class RotaGenerationError(Exception):
    """Custom exception for critical errors during rota generation."""
    pass

# --- Rota Generation Class ---
class RotaGenerator:
    """
    Encapsulates the logic and state for generating a rota for a given period.
    
    This class-based approach improves maintainability by:
    1.  Centralizing configuration (shift names, rules).
    2.  Managing state (member assignments, counts) internally.
    3.  Breaking down the complex generation logic into smaller, testable methods.
    """
    DEFAULT_CONFIG = {
        "LINKED_SHIFTS": {
            "Night": "Night Off"  # Member on "Night" must be on "Night Off" next week.
        },
        # Defines which roles are exempt from which shifts.
        # Here, 'role_id' corresponds to the 'is_admin' field in the Team model.
        # This is more explicit than using magic numbers in the code.
        "EXEMPTIONS_BY_ROLE": {
            1: ["Night", "Evening"],  # Admins exempt from Night and Evening
            2: ["Evening"],           # 'Evening Exempt' role
            3: ["Night"],             # 'Night Exempt' role
        }
    }

    def __init__(self, start_date: date, period_weeks: int, config: Optional[Dict] = None):
        self.start_date = start_date
        self.period_weeks = period_weeks
        self.config = self.DEFAULT_CONFIG.copy()
        if config:
            self.config.update(config)

        # --- Database Fetched Data ---
        self.all_members: List[Team] = db.session.query(Team).all()
        self.shifts: List[Shift] = db.session.query(Shift).all()
        self.default_shift: Shift = self._get_default_shift()
        self.special_shifts: List[Shift] = sorted(
            [s for s in self.shifts if s != self.default_shift],
            key=lambda s: s.min_members
        )

        # --- Internal State ---
        self.rota_id: int = self._generate_unique_rota_id()
        self.member_states: Dict[str, Dict[str, Any]] = self._initialize_member_states()
        self.last_week_assignments: Dict[str, str] = {} # {shift_name: member_name}

        self._validate_prerequisites()

    def _validate_prerequisites(self):
        """Ensures the database has the required data before starting."""
        if not self.all_members:
            raise RotaGenerationError("Cannot generate rota. No members found in the database.")
        if not self.shifts:
            raise RotaGenerationError("Cannot generate rota. No shifts found. Please define shifts first.")
        
        # Validate that all shifts mentioned in config actually exist
        all_db_shift_names = {s.name for s in self.shifts}
        for key_shift, val_shift in self.config.get("LINKED_SHIFTS", {}).items():
            if key_shift not in all_db_shift_names or val_shift not in all_db_shift_names:
                raise RotaGenerationError(f"Configuration error: Linked shift '{key_shift}' or '{val_shift}' not found in DB.")

    def _get_default_shift(self) -> Shift:
        """Identifies the default shift, typically the one with the most capacity."""
        if not self.shifts:
            raise RotaGenerationError("No shifts available to determine the default shift.")
        return max(self.shifts, key=lambda s: s.max_members)

    def _initialize_member_states(self) -> Dict[str, Dict[str, Any]]:
        """Sets up the initial state tracker for each member."""
        return {
            m.name: {
                'counts': {s.name: 0 for s in self.shifts},
                'total_special_shifts': 0
            } for m in self.all_members
        }

    def _generate_unique_rota_id(self) -> int:
        """Generates a unique 10-digit rota ID."""
        while True:
            rota_id = random.randint(10**9, 10**10 - 1)
            if not db.session.query(Rota).filter_by(rota_id=rota_id).first():
                return rota_id

    def _filter_eligible_members(self, week_start_date: date) -> List[Team]:
        """Filters out members on leave for a given week."""
        week_end_date = week_start_date + timedelta(days=6)
        eligible = []
        for member in self.all_members:
            on_leave = db.session.query(Leave).filter(
                Leave.member_id == member.id,
                Leave.start_date <= week_end_date,
                Leave.end_date >= week_start_date
            ).first()
            if not on_leave:
                eligible.append(member)
            else:
                logger.info(f"Member {member.name} is on leave from {on_leave.start_date} to {on_leave.end_date} and will be excluded.")
        return eligible

    def _is_member_exempt(self, member: Team, shift: Shift) -> bool:
        """Checks if a member's role exempts them from a specific shift."""
        exemptions = self.config["EXEMPTIONS_BY_ROLE"]
        # member.is_admin is used as the role_id here
        member_exempt_shifts = exemptions.get(member.is_admin, [])
        return shift.name in member_exempt_shifts

    def _select_fairest_member(self, candidates: List[Team], shift: Shift) -> Optional[Team]:
        """
        Picks the best candidate for a shift based on fairness criteria.
        - Prioritizes members who have worked this shift the fewest times.
        - Then prioritizes members who have worked the fewest total special shifts.
        """
        if not candidates:
            return None

        candidates.sort(key=lambda m: (
            self.member_states[m.name]['counts'][shift.name],
            self.member_states[m.name]['total_special_shifts'],
            m.name  # Stable tie-breaker
        ))
        return candidates[0]

    def _generate_week(self, week_start_date: date):
        """Generates and saves the rota for a single week."""
        logger.info(f"--- Generating Rota for Week: {week_start_date.strftime('%Y-%m-%d')} ---")
        
        eligible_members = self._filter_eligible_members(week_start_date)
        if not eligible_members:
            raise RotaGenerationError(f"No eligible members available for the week starting {week_start_date}.")

        assignments: Dict[str, List[Team]] = {shift.name: [] for shift in self.shifts}
        available_members: Set[Team] = set(eligible_members)

        # Step 1: Handle Mandatory Linked Shifts (e.g., Night -> Night Off)
        self._handle_linked_shifts(assignments, available_members)
        
        # Step 2: Fill minimum requirements for all special shifts
        self._fill_special_shifts(assignments, available_members)

        # Step 3: Assign all remaining members to the default shift
        assignments[self.default_shift.name].extend(list(available_members))

        # Step 4: Validate, save, and update state for the next week
        self._save_and_update_state(week_start_date, assignments)

    def _handle_linked_shifts(self, assignments: Dict[str, List[Team]], available_members: Set[Team]):
        """Assigns members based on the previous week's assignments for linked shifts."""
        linked_shifts = self.config.get("LINKED_SHIFTS", {})
        for from_shift, to_shift in linked_shifts.items():
            member_name = self.last_week_assignments.get(from_shift)
            if not member_name:
                continue

            member = next((m for m in available_members if m.name == member_name), None)
            if member:
                logger.info(f"Assigning {member.name} to {to_shift} (mandatory follow-up to {from_shift}).")
                assignments[to_shift].append(member)
                available_members.remove(member)

    def _fill_special_shifts(self, assignments: Dict[str, List[Team]], available_members: Set[Team]):
        """Fills the minimum number of spots for each special shift based on fairness."""
        for shift in self.special_shifts:
            # Skip shifts that might have been pre-filled by linked shift logic
            while len(assignments[shift.name]) < shift.min_members:
                candidates = [
                    m for m in available_members if not self._is_member_exempt(m, shift)
                ]
                if not candidates:
                    raise RotaGenerationError(f"Not enough eligible members to fill '{shift.name}' for week.")

                best_candidate = self._select_fairest_member(candidates, shift)
                if not best_candidate:
                    # This should ideally not happen if candidates list is not empty
                    raise RotaGenerationError(f"Could not select a candidate for '{shift.name}'.")
                
                logger.info(f"Assigning {best_candidate.name} to {shift.name} based on fairness.")
                assignments[shift.name].append(best_candidate)
                available_members.remove(best_candidate)

    def _save_and_update_state(self, week_start_date: date, assignments: Dict[str, List[Team]]):
        """Saves weekly assignments to the DB and updates the generator's state."""
        # Validation
        for shift in self.shifts:
            if len(assignments[shift.name]) < shift.min_members:
                raise RotaGenerationError(f"'{shift.name}' has {len(assignments[shift.name])} members, but requires {shift.min_members}.")
            if len(assignments[shift.name]) > shift.max_members:
                logger.warning(f"'{shift.name}' has {len(assignments[shift.name])} members, exceeding max of {shift.max_members}.")
        
        current_week_assignments = {}
        shift_map = {s.name: s for s in self.shifts}

        for shift_name, members in assignments.items():
            shift_obj = shift_map[shift_name]
            is_special = shift_obj != self.default_shift
            
            for member in members:
                # Save to DB
                db.session.add(RotaAssignment(
                    rota_id=self.rota_id,
                    week_start_date=week_start_date,
                    member_id=member.id,
                    shift_id=shift_obj.id
                ))
                # Update in-memory state for next week's fairness calculation
                self.member_states[member.name]['counts'][shift_name] += 1
                if is_special:
                    self.member_states[member.name]['total_special_shifts'] += 1
            
            # Record assignment for the next iteration's `last_week_assignments`
            # This is typically for single-person shifts like 'Night'
            if len(members) == 1:
                current_week_assignments[shift_name] = members[0].name
        
        self.last_week_assignments = current_week_assignments

    def generate(self) -> int:
        """
        Main execution method to generate the full rota period.
        Orchestrates the week-by-week generation.
        """
        # Create the main Rota entry
        end_date = self.start_date + timedelta(days=(self.period_weeks * 7) - 1)
        new_rota = Rota(rota_id=self.rota_id, start_date=self.start_date, end_date=end_date)
        db.session.add(new_rota)

        try:
            for week_num in range(self.period_weeks):
                current_week_start = self.start_date + timedelta(weeks=week_num)
                self._generate_week(current_week_start)
            
            db.session.commit()
            logger.info(f"Successfully generated and saved Rota ID: {self.rota_id}.")
            return self.rota_id

        except RotaGenerationError as e:
            logger.error(f"A critical error occurred: {e}")
            logger.error("Generation aborted. Rolling back database changes for this rota.")
            db.session.rollback()
            raise  # Re-raise the exception to be caught by the caller
        
        except Exception as e:
            logger.error(f"An unexpected error occurred: {e}")
            db.session.rollback()
            raise RotaGenerationError("An unexpected server error stopped rota generation.") from e


# --- Public Interface Function ---

def generate_period_rota(start_date: date, period_weeks: int) -> Optional[int]:
    """
    High-level function to instantiate and run the rota generator.
    
    Args:
        start_date: The start date of the rota period.
        period_weeks: The number of weeks to generate.

    Returns:
        The generated rota_id on success, None on failure.
    """
    logger.info(f"Starting rota generation for {period_weeks} weeks from {start_date}.")
    try:
        generator = RotaGenerator(start_date=start_date, period_weeks=period_weeks)
        rota_id = generator.generate()
        display_rota_table(rota_id) # Display the result in the console
        return rota_id
    except RotaGenerationError as e:
        # Errors are already logged by the generator, so we just return None.
        # The caller (e.g., the Flask route) can then flash a message.
        return None


# --- Presentation/Utility Function ---
# NOTE: In a larger application, this function would ideally be moved to a
# separate 'reporting.py' or 'utils.py' module to separate presentation
# logic from the core business logic of rota generation.

def display_rota_table(rota_id: int):
    """Displays the fully generated rota in a colorful, tabulated format."""
    logger.info(f"--- Rota ID: {rota_id} ---")
    
    assignments = db.session.query(RotaAssignment).filter_by(rota_id=rota_id).order_by(RotaAssignment.week_start_date).all()
    if not assignments:
        print(f"{Fore.RED}No rota entries found for Rota ID: {rota_id}{Style.RESET_ALL}")
        return

    shifts = db.session.query(Shift).order_by(Shift.start_time).all()
    shift_names = [s.name for s in shifts]

    colors = [Fore.YELLOW, Fore.MAGENTA, Fore.CYAN, Fore.LIGHTBLUE_EX, Fore.LIGHTGREEN_EX]
    headers = [f"{Fore.CYAN}📅 Week Range{Style.RESET_ALL}"] + [
        f"{colors[i % len(colors)]}{name}{Style.RESET_ALL}" for i, name in enumerate(shift_names)
    ]

    weekly_data = defaultdict(lambda: {name: [] for name in shift_names})
    for assign in assignments:
        weekly_data[assign.week_start_date][assign.shift.name].append(assign.member.name)
        
    table_data = []
    for week_start, shift_assignments in sorted(weekly_data.items()):
        week_end = week_start + timedelta(days=6)
        week_label = f"{Fore.LIGHTWHITE_EX}{week_start.strftime('%d %b')} - {week_end.strftime('%d %b %Y')}{Style.RESET_ALL}"
        row = [week_label]
        for i, name in enumerate(shift_names):
            members_str = ", ".join(sorted(shift_assignments[name])) or "-"
            row.append(f"{colors[i % len(colors)]}{members_str}{Style.RESET_ALL}")
        table_data.append(row)
        
    print(tabulate(table_data, headers=headers, tablefmt="fancy_grid", stralign="center"))