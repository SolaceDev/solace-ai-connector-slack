import base64
import re
from datetime import datetime

from prettytable import PrettyTable

from solace_ai_connector.common.log import log
from .slack_base import SlackBase


info = {
    "class_name": "SlackOutput",
    "description": (
        "Slack output component. The component sends messages to Slack channels using the Bolt API."
    ),
    "config_parameters": [
        {
            "name": "slack_bot_token",
            "type": "string",
            "description": "The Slack bot token to connect to Slack.",
        },
        {
            "name": "slack_app_token",
            "type": "string",
            "description": "The Slack app token to connect to Slack.",
        },
        {
            "name": "share_slack_connection",
            "type": "string",
            "description": "Share the Slack connection with other components in this instance.",
        },
        {
            "name": "correct_markdown_formatting",
            "type": "boolean",
            "description": "Correct markdown formatting in messages to conform to Slack markdown.",
            "default": "true",
        },
    ],
    "input_schema": {
        "type": "object",
        "properties": {
            "message_info": {
                "type": "object",
                "properties": {
                    "channel": {
                        "type": "string",
                    },
                    "type": {
                        "type": "string",
                    },
                    "user_email": {
                        "type": "string",
                    },
                    "client_msg_id": {
                        "type": "string",
                    },
                    "ts": {
                        "type": "string",
                    },
                    "subtype": {
                        "type": "string",
                    },
                    "event_ts": {
                        "type": "string",
                    },
                    "channel_type": {
                        "type": "string",
                    },
                    "user_id": {
                        "type": "string",
                    },
                    "session_id": {
                        "type": "string",
                    },
                },
                "required": ["channel", "session_id"],
            },
            "content": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                    },
                    "files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                },
                                "content": {
                                    "type": "string",
                                },
                                "mime_type": {
                                    "type": "string",
                                },
                                "filetype": {
                                    "type": "string",
                                },
                                "size": {
                                    "type": "number",
                                },
                            },
                        },
                    },
                },
            },
        },
        "required": ["message_info", "content"],
    },
}


class SlackOutput(SlackBase):
    def __init__(self, **kwargs):
        super().__init__(info, **kwargs)
        self.fix_formatting = self.get_config("correct_markdown_formatting", True)
        self.streaming_state = {}
        self.blocks = []

    def invoke(self, message, data):
        message_info = data.get("message_info")
        content = data.get("content")
        text = content.get("text")
        stream = content.get("stream")
        first_streamed_chunk = content.get("first_streamed_chunk")
        last_streamed_chunk = content.get("last_streamed_chunk")
        uuid = content.get("uuid")
        channel = message_info.get("channel")
        thread_ts = message_info.get("ts")
        ack_msg_ts = message_info.get("ack_msg_ts")

        if not channel:
            log.error("slack_output: No channel specified in message")
            self.discard_current_message()
            return None

        return {
            "channel": channel,
            "text": text,
            "files": content.get("files"),
            "thread_ts": thread_ts,
            "ack_msg_ts": ack_msg_ts,
            "stream": stream,
            "first_streamed_chunk": first_streamed_chunk,
            "last_streamed_chunk": last_streamed_chunk,
            "uuid": uuid,
        }

    def send_message(self, message):
        try:
            channel = message.get_data("previous:channel")
            messages = message.get_data("previous:text")
            stream = message.get_data("previous:stream")
            files = message.get_data("previous:files") or []
            thread_ts = message.get_data("previous:ts")
            ack_msg_ts = message.get_data("previous:ack_msg_ts")
            first_streamed_chunk = message.get_data("previous:first_streamed_chunk")
            last_streamed_chunk = message.get_data("previous:last_streamed_chunk")
            uuid = message.get_data("previous:uuid")

            if not isinstance(messages, list):
                if messages is not None:
                    messages = [messages]
                else:
                    messages = []

            table_content = ""

            for index, text in enumerate(messages):
                if not text or not isinstance(text, str):
                    continue

                if self.fix_formatting:
                    text = self.fix_markdown(text)

                table_content += text

                if last_streamed_chunk:
                    # Process the accumulated table
                    table_content= self.convert_markdown_tables(table_content)
                    # Reset the accumulator after processing
                    table_content = ""

                if index != 0:
                    text = "\n" + text

                if first_streamed_chunk:
                    streaming_state = self.add_streaming_state(uuid)
                else:
                    streaming_state = self.get_streaming_state(uuid)
                    if not streaming_state:
                        streaming_state = self.add_streaming_state(uuid)

                if stream:
                    if streaming_state.get("completed"):
                        # We can sometimes get a message after the stream has completed
                        continue

                    streaming_state["completed"] = last_streamed_chunk
                    ts = streaming_state.get("ts")
                    if ts:
                        try:
                            self.app.client.chat_update(
                                channel=channel, ts=ts, text=text, blocks=self.blocks, unfurl_links=False
                            )
                        except Exception:
                            # It is normal to possibly get an update after the final
                            # message has already arrived and deleted the ack message
                            pass
                    else:
                        response = self.app.client.chat_postMessage(
                            channel=channel, text=text, thread_ts=thread_ts, blocks=self.blocks, unfurl_links=False
                        )
                        streaming_state["ts"] = response["ts"]

                else:
                    # Not streaming
                    ts = streaming_state.get("ts")
                    streaming_state["completed"] = True
                    if not ts:
                        self.app.client.chat_postMessage(
                            channel=channel,
                            text=text,
                            thread_ts=thread_ts,
                            blocks=self.blocks,
                            unfurl_links=False,
                        )
                    # if ts:
                    #     self.app.client.chat_update(channel=channel, ts=ts, text=text)
                    # else:
                    #     self.app.client.chat_postMessage(
                    #         channel=channel, text=text, thread_ts=thread_ts
                    #     )

            for file in files:
                file_content = base64.b64decode(file["content"])
                self.app.client.files_upload_v2(
                    channel=channel,
                    file=file_content,
                    thread_ts=thread_ts,
                    filename=file["name"],
                )
        except Exception as e:
            log.error("Error sending slack message: %s", e)

        super().send_message(message)

        try:
            if ack_msg_ts:
                self.app.client.chat_delete(channel=channel, ts=ack_msg_ts)
        except Exception:
            pass

    def fix_markdown(self, message):
        self.blocks = []
        # Fix links - the LLM is very stubborn about giving markdown links
        # Find [text](http...) and replace with <http...|text>
        message = re.sub(r"\[(.*?)\]\((http.*?)\)", r"<\2|\1>", message)
        # Remove the language specifier from code blocks
        message = re.sub(r"```[a-z]+\n", "```", message)
        # Fix bold
        message = re.sub(r"\*\*(.*?)\*\*", r"*\1*", message)

        # Reformat a table to be Slack compatible
        message = self.convert_markdown_tables(message)

        return message

    def get_streaming_state(self, uuid):
        return self.streaming_state.get(uuid)

    def add_streaming_state(self, uuid):
        state = {
            "create_time": datetime.now(),
        }
        self.streaming_state[uuid] = state
        self.age_out_streaming_state()
        return state

    def delete_streaming_state(self, uuid):
        try:
            del self.streaming_state[uuid]
        except KeyError:
            pass

    def age_out_streaming_state(self, age=60):
        # Note that we can later optimize this by using an array of streaming_state that
        # is ordered by create_time and then we can just remove the first element until
        # we find one that is not expired.
        now = datetime.now()
        for uuid, state in list(self.streaming_state.items()):
            if (now - state["create_time"]).total_seconds() > age:
                del self.streaming_state[uuid]

    def convert_markdown_tables(self, message):
        def markdown_to_fixed_width(match):

            table_str = match.group(0)
            rows = [
                line.strip().split("|")
                for line in table_str.split("\n")
                if line.strip()
            ]
            headers = [cell.strip() for cell in rows[0] if cell.strip()]
            num_rows = len(rows) - 2  # Adjust for header and separator rows

            if num_rows <= 5:
                # self.convert_small_table(rows, headers)
                self.blocks= self.convert_small_table(rows, headers)
                return ""
            
            else:
                pt = PrettyTable()
                pt.field_names = headers

                for row in rows[2:]:
                    pt.add_row([cell.strip() for cell in row if cell.strip()])

                return f"\n```\n{pt.get_string()}\n```\n"

        def table_wrapper(match):
            return markdown_to_fixed_width(match)
        
        pattern = r"\|.*\|[\n\r]+\|[-:| ]+\|[\n\r]+((?:\|.*\|[\n\r]+)+)"
        return re.sub(pattern, table_wrapper, message)
    
    def convert_small_table(self, rows, headers):
        blocks = []
        
        filtered_rows = [
            [re.sub(r"http\S+", "", cell).strip() for cell in row if cell.strip() != ''] for row in rows
        ]

        headers = filtered_rows[0]
        data_rows = filtered_rows[2:]

        for row in data_rows:
            formatted_row = ""
            jira_key = ""
            for index, (header, cell) in enumerate(zip(headers, row)):
                if index == 0:
                    if "jira" in header.lower() and "key" in header.lower():
                        jira_key = cell.strip()
                        continue
                    else:
                        formatted_row += f"*{cell.strip()}*\n"

                if index == 1 and jira_key and "summary" in header.lower():
                    summary = cell.strip()
                    link = f"https://sol-jira.atlassian.net/browse/{jira_key}"
                    formatted_row += f"<{link}|*[{jira_key}] {summary}*>\n"

                else:
                    if index != 0:
                        formatted_row += f"{header.strip()}: {cell.strip()}\n"
            
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": formatted_row.strip()
                }
            })
        
        return blocks