"""Base class for all Slack components"""

from abc import ABC, abstractmethod
import json
import os
import requests

from slack_bolt import App  # pylint: disable=import-error
from solace_ai_connector.components.component_base import ComponentBase


class SlackBase(ComponentBase, ABC):
    _slack_apps = {}

    def __init__(self, module_info, **kwargs):
        super().__init__(module_info, **kwargs)
        self.slack_bot_token = self.get_config("slack_bot_token")
        self.slack_app_token = self.get_config("slack_app_token")
        self.max_file_size = self.get_config("max_file_size", 20)
        self.max_total_file_size = self.get_config("max_total_file_size", 20)
        self.share_slack_connection = self.get_config("share_slack_connection")
        self.feedback_enabled = self.get_config("feedback", False)
        self.feedback_post_url = self.get_config("feedback_post_url", None)
        self.feedback_post_headers = self.get_config("feedback_post_headers", {})

        if self.share_slack_connection:
            if self.slack_bot_token not in SlackBase._slack_apps:
                self.app = App(token=self.slack_bot_token)
                SlackBase._slack_apps[self.slack_bot_token] = self.app
            else:
                self.app = SlackBase._slack_apps[self.slack_bot_token]
        else:
            self.app = App(token=self.slack_bot_token)

    @abstractmethod
    def invoke(self, message, data):
        pass

    def __str__(self):
        return self.__class__.__name__ + " " + str(self.config)

    def __repr__(self):
        return self.__str__()
    
    def register_action_handlers(self):
        @self.app.action("thumbs_up_action")
        def handle_thumbs_up(ack, body, say):
            self.feedback(ack, body, say, "thumbs_up")

        @self.app.action("thumbs_down_action")
        def handle_thumbs_down(ack, body, say):
            self.feedback(ack, body, say, "thumbs_down")

    def feedback(self, ack, body, say, feedback):
        # Acknowledge the action request
        ack()
        # Respond to the action
        value_object = json.loads(body['actions'][0]['value'])
        say(f"Thanks for the feedback, <@{body['user']['id']}>!")

        rest_body = {
            "user": body['user'],
            "feedback": feedback,
            "interface": "slack",
            "interface_data": {
                "channel": body['channel']
            },
            "message": body['message'],
            "data": value_object
        }

        try:
            response = requests.post(
                url=self.feedback_post_url,
                headers=self.feedback_post_headers,
                data=json.dumps(rest_body)
            )
        except Exception as e:
            self.logger.error(f"Failed to post feedback: {str(e)}")
