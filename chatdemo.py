#!/usr/bin/env python3
#
# Copyright 2009 Facebook
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import re
import urllib.parse
import logging
import time
import tornado.auth
import tornado.escape
import tornado.ioloop
import tornado.httpserver
import tornado.options
import tornado.web
import os.path
import uuid
import hashlib
from functools import lru_cache, partial

from tornado.options import define, options

define("port", default=8888, help="run on the given port", type=int)

online_users = {}
POLL_TIME = 120  # seconds


@lru_cache()
def md5sum(s):
  m = hashlib.md5()
  m.update(s.encode('utf-8'))
  return m.hexdigest()


class Application(tornado.web.Application):
  def __init__(self):
    handlers = [
      (r"/", MainHandler),
      (r"/auth/login", AuthLoginHandler),
      (r"/auth/logout", AuthLogoutHandler),
      (r"/a/message/new", MessageNewHandler),
      (r"/a/message/updates", MessageUpdatesHandler),
    ]
    settings = dict(
      cookie_secret="43oETzKXQAGaY9kL5gnmGeJJFuYh7EQnp2XdTP1o/Vo=",
      login_url="/auth/login",
      template_path=os.path.join(os.path.dirname(__file__), "templates"),
      static_path=os.path.join(os.path.dirname(__file__), "static"),
      xsrf_cookies=True,
      autoescape="xhtml_escape",
      debug=True,
    )
    tornado.web.Application.__init__(self, handlers, **settings)


class BaseHandler(tornado.web.RequestHandler):
  def get_current_user(self):
    user = self.get_secure_cookie("user")
    if not user:
      return None
    return tornado.escape.json_decode(user)

  def initialize(self):
    if self.current_user:
      # new user
      online_users[self.current_user['nick']] = {
        'timeout': time.time() + 2 * POLL_TIME
      }

  def redirect(self, url, permanent=False):
    super().redirect(urllib.parse.urljoin(self.request.full_url(), url),
                     permanent)


class MainHandler(BaseHandler):
  @tornado.web.authenticated
  def get(self):
    avatar = 'https://secure.gravatar.com/avatar/%s?size=18' % \
        md5sum(self.current_user['email'])
    self.render("index.html", messages=MessageMixin.cache,
                name=self.current_user['nick'], avatar=avatar)


class MessageMixin(object):
  waiters = set()
  cache = []
  cache_size = 200

  def wait_for_messages(self, callback, cursor=None):
    cls = MessageMixin
    if cursor:
      index = 0
      for i in range(len(cls.cache)):
        index = len(cls.cache) - i - 1
        if cls.cache[index]["id"] == cursor:
          break
      recent = cls.cache[index + 1:]
      if recent:
        callback(recent)
        return
    cls.waiters.add(callback)

  def cancel_wait(self, callback):
    cls = MessageMixin
    cls.waiters.remove(callback)

  def broadcasting(self, messages):
    cls = MessageMixin
    logging.info("Sending new message to %r listeners", len(cls.waiters))
    logging.info("online users: %s, sender %s",
                 tuple(online_users.keys()), self.current_user['nick'])
    for callback in cls.waiters:
      try:
        callback(messages)
      except:
        logging.error("Error in waiter callback", exc_info=True)
    cls.waiters = set()
    cls.cache.extend(messages)
    if len(cls.cache) > self.cache_size:
      cls.cache = cls.cache[-self.cache_size:]


class CommandMixin:
  def unknown(self, message, cmd):
    'Respond to an unknown command'
    message["body"] = '未知命令: %s' % cmd
    message["html"] = self.render_string("message.html", message=message)
    self.write(message)

  def handle(self):
    message = {
      "id": str(uuid.uuid4()),
      "from": self.current_user['nick'],
      "body": self.get_argument("body", strip=False).replace(' ', ' '),
      "time": time.strftime('%H:%M:%S'),
    }

    message["avatar"] = 'https://secure.gravatar.com/avatar/%s' % \
        md5sum(self.current_user['email'])

    message["avatar_small"] = message["avatar"] + '?size=18'
    message["avatar"] = message["avatar"] + '?size=512'

    re_cmd = re.compile(r'^\s*/(.+)')
    cmd = re_cmd.findall(message["body"])
    if len(cmd):
      cmd = cmd[0].strip()
    else:
      cmd = 'say'
    try:
      command = getattr(self, 'do_' + cmd, None)
      command(message)
    except TypeError:
      self.unknown(message, cmd)

  def do_say(self, message):
    message["html"] = self.render_string("message.html", message=message)
    self.write(message)
    self.broadcasting([message])

  def do_online(self, message):
    users = online_users.keys()
    message["body"] = "%d 人在线：" % len(users) + ', '.join(users)
    message["html"] = self.render_string("message.html", message=message)
    self.write(message)

  def do_logout(self, message):
    try:
      del online_users[self.current_user['nick']]
      self.clear_cookie("user")
    except KeyError:
      pass


class MessageNewHandler(BaseHandler, MessageMixin, CommandMixin):
  @tornado.web.authenticated
  def post(self):
    self.handle()


class MessageUpdatesHandler(BaseHandler, MessageMixin):
  @tornado.web.authenticated
  @tornado.web.asynchronous
  def post(self):
    cursor = self.get_argument("cursor", None)
    self.wait_for_messages(self.on_new_messages, cursor=cursor)
    ioloop.add_timeout(time.time() + POLL_TIME,
                       partial(self.timedout, self.on_new_messages))

  def timedout(self, callback):
    if not (self._finished or self.request.connection.stream.closed()):
      try:
        MessageMixin.waiters.remove(callback)
      except ValueError:
        logging.warn('timedout request callback not in waiters: %s, %r',
                     self.current_user, callback)
      self.finish(dict(status='try again'))
      online_users[self.current_user['nick']]['timeout'] = time.time() + \
          2 * POLL_TIME

  def on_new_messages(self, messages):
    # Closed client connection
    if self.request.connection.stream.closed():
      try:
        del online_users[self.current_user['nick']]
      except KeyError:
        pass
      return
    self.finish(dict(messages=messages, status='ok'))
    try:
      online_users[self.current_user['nick']]['timeout'] = time.time() + \
          2 * POLL_TIME
    except KeyError:
      logging.warn("user %s login wasn't be caught", self.current_user)

  def on_connection_close(self):
    self.cancel_wait(self.on_new_messages)


class AuthLoginHandler(BaseHandler):
  @tornado.web.asynchronous
  def get(self):
    self.render("login.html")

  def post(self):
    nick = self.get_argument("nick", None)
    email = self.get_argument("email", '')
    if not nick:
      self.render("login.html")
    elif nick in online_users:
      self.render("login.html", error="昵称已被使用")
    else:
      user = {
        'nick': nick,
        'email': email,
      }
      self.set_secure_cookie("user", tornado.escape.json_encode(user),
                             expires_days=1)
      self.redirect(self.get_argument("next", "/"))


class AuthLogoutHandler(BaseHandler):
  def get(self):
    try:
      del online_users[self.current_user['nick']]
    except KeyError:
      pass
    except TypeError:
      pass
    self.clear_cookie("user")
    self.render("logout.html")


def checkOnlineUsers():
  now = time.time()
  for k, v in online_users.copy().items():
    if v['timeout'] < now:
      del online_users[k]


def main(ssl=False):
  tornado.options.parse_command_line()
  app = Application()
  if ssl:
    http_server = tornado.httpserver.HTTPServer(app, ssl_options={
      "certfile": os.path.expanduser("~/etc/key/server.crt"),
      "keyfile": os.path.expanduser("~/etc/key/server.key"),
    })
    http_server.listen(options.port)
  else:
    http_server = tornado.httpserver.HTTPServer(app)
    http_server.listen(options.port)
  global ioloop
  ioloop = tornado.ioloop.IOLoop.instance()
  tornado.ioloop.PeriodicCallback(checkOnlineUsers, POLL_TIME * 100).start()
  ioloop.start()

if __name__ == "__main__":
  try:
    main()
  except KeyboardInterrupt:
    pass
