from django.http import JsonResponse


class SuccessResponse:
    def __init__(self, message, status_code=200, data=None):
        self.message = message
        self.status_code = status_code
        self.data = data
        self.success = True

    def to_json_response(self):
        return JsonResponse({
            'message': self.message,
            'success': self.success,
            'status': self.status_code,
            'data': self.data
        }, status=self.status_code)


class ErrorResponse:
    def __init__(self, message, status_code=400):
        self.message = message
        self.status_code = status_code
        self.success = False

    def to_json_response(self):
        return JsonResponse({
            'message': self.message,
            'success': self.success,
            'status': self.status_code
        }, status=self.status_code)