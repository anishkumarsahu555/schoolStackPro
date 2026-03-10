"""Compatibility wrappers for event web push notifications."""

from homeApp.push_service import send_event_push_notifications


def notify_event_added(event):
    send_event_push_notifications(event, action='added')


def notify_event_updated(event):
    send_event_push_notifications(event, action='updated')
