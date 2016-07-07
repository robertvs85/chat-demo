#!/usr/bin/env python
#
# Copyright 2009 Facebook
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#	 http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
"""Simplified chat demo for websockets.
Authentication, error handling, etc are left as an exercise for the reader :)
"""

import logging
import tornado.escape
import tornado.ioloop
import tornado.options
import tornado.web
import tornado.websocket
import os.path
import uuid

from tornado.options import define, options

define("port", default=8888, help="run on the given port", type=int)


class Application(tornado.web.Application):
	def __init__(self):
		handlers = [
			(r"/chat/(.*)", MainHandler),
			(r"/chatsocket/(.*)", ChatSocketHandler),
		]
		settings = dict(
			cookie_secret="__TODO:_GENERATE_YOUR_OWN_RANDOM_VALUE_HERE__",
			template_path=os.path.join(os.path.dirname(__file__), "templates"),
			static_path=os.path.join(os.path.dirname(__file__), "static"),
			xsrf_cookies=True
		)
		super(Application, self).__init__(handlers, **settings)

uniq_users = {}

class MainHandler(tornado.web.RequestHandler):
	def get(self, chat_id):
		self.render("new_user.html", chat_id=chat_id)
			
	def post(self, chat_id):
		
		if chat_id not in uniq_users:
			uniq_users[chat_id] = set()
		
		username = self.get_argument("username")
		if username in uniq_users[chat_id]:
			self.render("new_user.html", chat_id=chat_id)
		else:
			uniq_users[chat_id].add(username)
			self.render("index.html", messages=ChatSocketHandler.cache[chat_id] if chat_id in ChatSocketHandler.cache else [], chat_id=chat_id, username=username)
	

class ChatSocketHandler(tornado.websocket.WebSocketHandler):
	waiters = {}
	cache = {}
	cache_size = 200

	@tornado.web.asynchronous
	def get(self, *args, **kwargs):
		self.open_args = args
		self.open_kwargs = kwargs
		self.chat_id = args[0]
		self.username = self.get_argument('username')
	
		# Upgrade header should be present and should be equal to WebSocket
		if self.request.headers.get("Upgrade", "").lower() != 'websocket':
			self.set_status(400)
			log_msg = "Can \"Upgrade\" only to \"WebSocket\"."
			self.finish(log_msg)
			gen_log.debug(log_msg)
			return
	
		# Connection header should be upgrade.
		# Some proxy servers/load balancers
		# might mess with it.
		headers = self.request.headers
		connection = map(lambda s: s.strip().lower(),
				 headers.get("Connection", "").split(","))
		if 'upgrade' not in connection:
			self.set_status(400)
			log_msg = "\"Connection\" must be \"Upgrade\"."
			self.finish(log_msg)
			gen_log.debug(log_msg)
			return
	
		# Handle WebSocket Origin naming convention differences
		# The difference between version 8 and 13 is that in 8 the
		# client sends a "Sec-Websocket-Origin" header and in 13 it's
		# simply "Origin".
		if "Origin" in self.request.headers:
			origin = self.request.headers.get("Origin")
		else:
			origin = self.request.headers.get("Sec-Websocket-Origin", None)
	
		# If there was an origin header, check to make sure it matches
		# according to check_origin. When the origin is None, we assume it
		# did not come from a browser and that it can be passed on.
		if origin is not None and not self.check_origin(origin):
			self.set_status(403)
			log_msg = "Cross origin websockets not allowed"
			self.finish(log_msg)
			gen_log.debug(log_msg)
			return
	
		self.stream = self.request.connection.detach()
		self.stream.set_close_callback(self.on_connection_close)
	
		self.ws_connection = self.get_websocket_protocol()
		if self.ws_connection:
			self.ws_connection.accept_connection()
		else:
			if not self.stream.closed():
				self.stream.write(tornado.escape.utf8(
					"HTTP/1.1 426 Upgrade Required\r\n"
					"Sec-WebSocket-Version: 7, 8, 13\r\n\r\n"))
				self.stream.close()

	def get_compression_options(self):
		return {}

	#Stores the new connection so that updates can be sent
	def open(self, chat_id):
		if chat_id not in ChatSocketHandler.waiters:
			ChatSocketHandler.waiters[chat_id] = set()
		ChatSocketHandler.waiters[chat_id].add(self)
		
	#Removes the user and the connection (socket)
	def on_close(self):
		uniq_users[self.chat_id].remove(self.username)
		ChatSocketHandler.waiters[self.chat_id].remove(self)

	#Caching text so that users entering the channel can see old messages
	def update_cache(self, chat):
		if self.chat_id not in ChatSocketHandler.cache:
			ChatSocketHandler.cache[self.chat_id] = []
		ChatSocketHandler.cache[self.chat_id].append(chat)
		if len(ChatSocketHandler.cache[self.chat_id]) > ChatSocketHandler.cache_size:
			ChatSocketHandler.cache[self.chat_id] = ChatSocketHandler.cache[self.chat_id][-ChatSocketHandler.cache_size:]

	#Sends new messages to all users in the chat
	def send_updates(self, chat):
		logging.info("sending message to %d waiters", len(ChatSocketHandler.waiters[self.chat_id]))
		for waiter in ChatSocketHandler.waiters[self.chat_id]:
			try:
				waiter.write_message(chat)
			except:
				logging.error("Error sending message", exc_info=True)

	#Receives a new message from a participant and shares it with everyone else
	def on_message(self, message):
		logging.info("got message %r", message)
		parsed = tornado.escape.json_decode(message)
		chat = {
			"id": str(uuid.uuid4()),
			"body": parsed["body"],
			"username": self.username
			}
		chat["html"] = tornado.escape.to_basestring(
			self.render_string("message.html", message=chat))

		self.update_cache(chat)
		self.send_updates(chat)


def main():
	tornado.options.parse_command_line()
	app = Application()
	app.listen(options.port)
	tornado.ioloop.IOLoop.current().start()


if __name__ == "__main__":
	main()