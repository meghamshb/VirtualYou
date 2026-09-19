"""Default to the owner-approved VirtualYou workflow, not the unrestricted starter."""
from slack_bolt import App


def register_listeners(app: App):
    from virtualyou_workflow.listeners import register
    return register(app)
