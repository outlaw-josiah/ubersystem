from sqlalchemy.orm import joinedload, subqueryload

from uber.config import c
from uber.decorators import ajax, all_renderable, department_id_adapter
from uber.models import Attendee, HotelRequests, RoomAssignment, Shift


@all_renderable(c.PEOPLE)
class Root:
    @department_id_adapter
    def index(self, session, department_id=None):
        department_id = department_id or c.DEFAULT_DEPARTMENT_ID
        return {
            'department_id': department_id,
            'department_name': c.DEPARTMENTS[department_id],
            'checklist': session.checklist_status('hotel_eligible', department_id),
            'attendees': session.query(Attendee).filter(
                Attendee.hotel_eligible == True,
                Attendee.badge_status.in_([c.NEW_STATUS, c.COMPLETED_STATUS]),
                Attendee.dept_memberships.any(department_id=department_id)
            ).order_by(Attendee.full_name).all()
        }  # noqa: E712

    def mark_hotel_eligible(self, session, id):
        """
        Force mark a non-staffer as eligible for hotel space.
        This is outside the normal workflow, used for when we have a staffer
        that only has an attendee badge for some reason, and we want to mark
        them as being OK to crash in a room.
        """
        attendee = session.attendee(id)
        attendee.hotel_eligible = True
        session.commit()
        return '{} has now been overridden as being hotel eligible'.format(
            attendee.full_name)

    @department_id_adapter
    def requests(self, session, department_id=None):
        dept_filter = [] if not department_id \
            else [Attendee.dept_memberships.any(department_id=department_id)]

        requests = session.query(HotelRequests) \
            .join(HotelRequests.attendee) \
            .options(joinedload(HotelRequests.attendee)) \
            .filter(
                Attendee.badge_status.in_([c.NEW_STATUS, c.COMPLETED_STATUS]),
                *dept_filter) \
            .order_by(Attendee.full_name).all()

        room_access = set([c.ADMIN, c.STAFF_ROOMS])
        admin_has_room_access = bool(room_access.intersection(session.current_admin_account().access_ints))
        return {
            'admin_has_room_access': admin_has_room_access,
            'requests': requests,
            'department_id': department_id,
            'department_name': c.DEPARTMENTS.get(department_id, 'All'),
            'declined_count': len([r for r in requests if r.nights == '']),
            'checklist': session.checklist_status(
                'approve_setup_teardown', department_id),
            'staffer_count': session.query(Attendee).filter(
                Attendee.hotel_eligible == True, *dept_filter).count()  # noqa: E712
        }

    def hours(self, session):
        staffers = session.query(Attendee) \
            .filter(Attendee.hotel_eligible == True, Attendee.badge_status.in_([c.NEW_STATUS, c.COMPLETED_STATUS])) \
            .options(joinedload(Attendee.hotel_requests), subqueryload(Attendee.shifts).subqueryload(Shift.job)) \
            .order_by(Attendee.full_name).all()  # noqa: E712

        return {'staffers': [s for s in staffers if s.hotel_shifts_required and s.weighted_hours < c.HOTEL_REQ_HOURS]}

    def no_shows(self, session):
        room_assignments = session.query(RoomAssignment).options(
            joinedload(RoomAssignment.attendee).joinedload(Attendee.hotel_requests),
            joinedload(RoomAssignment.attendee).subqueryload(Attendee.room_assignments))
        staffers = [ra.attendee for ra in room_assignments if not ra.attendee.checked_in]
        return {'staffers': sorted(staffers, key=lambda a: a.full_name)}

    @ajax
    def approve(self, session, id, approved):
        hr = session.hotel_requests(id)
        if approved == 'approved':
            hr.approved = True
        else:
            hr.decline()
        session.commit()
        return {'nights': hr.nights_display}
