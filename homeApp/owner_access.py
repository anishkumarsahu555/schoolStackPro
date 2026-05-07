from django.db.models import Q


def school_owner_user_q(user_id):
    return Q(ownerID__userID_id=user_id) | Q(owners__userID_id=user_id)


def school_owner_q(owner):
    return Q(ownerID=owner) | Q(owners=owner)


def school_session_owner_user_q(user_id):
    return Q(schoolID__ownerID__userID_id=user_id) | Q(schoolID__owners__userID_id=user_id)
