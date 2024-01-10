from django.db.models.signals import pre_save
from django.dispatch import receiver

@receiver(pre_save)
def pre_save_callback(sender, instance, **kwargs):
    # if sender.__name__ != 'YourModel':  # Optional: Only apply the condition for specific models
    #     return
    print('hello')

    # your custom logic here
    # perform some action before saving
