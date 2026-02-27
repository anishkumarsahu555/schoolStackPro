from django.http import JsonResponse


class SuccessResponse:
    def __init__(self, message, status_code=200, data=None, extra=None):
        self.message = message
        self.status_code = status_code
        self.data = data
        self.extra = extra or {}
        self.success = True

    def to_json_response(self):
        payload = {
            'message': self.message,
            'success': self.success,
            'status': self.status_code,
            'data': self.data
        }
        payload.update(self.extra)
        return JsonResponse(payload, status=self.status_code)


class ErrorResponse:
    def __init__(self, message, status_code=400, data=None, extra=None):
        self.message = message
        self.status_code = status_code
        self.data = data
        self.extra = extra or {}
        self.success = False

    def to_json_response(self):
        payload = {
            'message': self.message,
            'success': self.success,
            'status': self.status_code,
            'data': self.data
        }
        payload.update(self.extra)
        return JsonResponse(payload, status=self.status_code)
