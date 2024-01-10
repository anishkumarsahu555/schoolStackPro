from dynamic_filenames import FilePattern

UPLOAD_TO_PATTERNS = FilePattern(
    filename_pattern='my_model/{app_label:.25}/{model_name:.30}/{uuid:base32}{ext}',
)
