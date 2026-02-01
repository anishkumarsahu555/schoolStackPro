from homeApp.models import SchoolDetail
from managementApp.models import TeacherDetail, Student
from utils.logger import logger


# get school id from teacher or student or SchoolDetail
def get_school_id(request):
    user_id = request.user.id
    try:
        teacher = TeacherDetail.objects.get(userID_id=user_id)
        logger.info(f"Teacher found: {teacher.name}")
        return teacher.schoolID_id
    except TeacherDetail.DoesNotExist:
        try:
            student = Student.objects.get(userID_id=user_id)
            logger.info(f"Student found: {student.name}")
            return student.schoolID_id
        except Student.DoesNotExist:
            try:
                school = SchoolDetail.objects.get(ownerID__userID_id=user_id)
                logger.info(f"School found: {school.schoolName}")
                return school.id
            except SchoolDetail.DoesNotExist:
                return None
    except Exception as e:
        logger.error(f"Error getting school id: {e}")
        return None

