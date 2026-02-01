import json
from utils.custom_response import SuccessResponse, ErrorResponse
from utils.logger import logger

def validate_input(required_fields=None, allow_extra=True):
    """
    Validates request data (query params, JSON, form-data, or multipart).
    - required_fields: list of required keys
    - allow_extra: whether to allow extra fields
    """
    def decorator(view_func):
        def wrapper(request, *args, **kwargs):
            data = {}

            # 0️⃣ Check query parameters first (for GET requests)
            if request.method == 'GET':
                data.update(request.GET.dict())

            # 1️⃣ Try JSON if content type is application/json
            if request.content_type and "application/json" in request.content_type:
                try:
                    data.update(json.loads(request.body.decode("utf-8")))
                except json.JSONDecodeError:
                    logger.error("Invalid JSON format")
                    return ErrorResponse("Invalid JSON format").to_json_response()
            # 2️⃣ Handle form-data / multipart for non-GET requests
            elif request.method != 'GET':
                data.update(request.POST.dict())
                # Include files if needed
                for file_key, file_obj in request.FILES.items():
                    data[file_key] = file_obj

            # 3️⃣ Required field check
            if required_fields:
                missing = [field for field in required_fields if not data.get(field)]
                if missing:
                    logger.error(f"Missing required fields: {', '.join(missing)}")
                    return ErrorResponse(f"Missing required fields: {', '.join(missing)}").to_json_response()

            # 4️⃣ Extra field check
            if not allow_extra and required_fields:
                extra_fields = set(data.keys()) - set(required_fields)
                if extra_fields:
                    logger.error(f"Unexpected fields: {', '.join(extra_fields)}")
                    return ErrorResponse(f"Unexpected fields: {', '.join(extra_fields)}").to_json_response()

            # Attach validated data to request
            request.input_data = data
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator