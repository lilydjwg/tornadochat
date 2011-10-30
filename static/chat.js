// Copyright 2009 FriendFeed
//
// Licensed under the Apache License, Version 2.0 (the "License"); you may
// not use this file except in compliance with the License. You may obtain
// a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
// WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
// License for the specific language governing permissions and limitations
// under the License.

var info;

var showinfo = function(what){
  var $info = $('#info');
  if(typeof what == "string"){
    $info.text(what).css({
      left: ((document.body.clientWidth - $info.outerWidth()) / 2) + 'px'
    }).slideDown();
  }else{
    if(!what){
      $info.slideUp();
    }
  }
};

var updateTitle = function(){
  if(info.unread){
    document.title = '('+info.unread+') ' + info.originalTitle;
  }else{
    document.title = info.originalTitle;
  }
};

var getMaxY = function(){
  return document.documentElement.scrollHeight - document.documentElement.clientHeight;
};

var scrollWindow = function(){
  var maxy = getMaxY();
  if(maxy != window.scrollY){
    amount = (maxy - window.scrollY) / 5;
    if(amount < 5){
      amount = 5;
    }
    window.scrollBy(0, amount);
    setTimeout(arguments.callee, 15, false);
  }
};

var getCookie = function(name){
  var r = document.cookie.match("\\b" + name + "=([^;]*)\\b");
  return r ? r[1] : undefined;
};

var updater = {
  errorSleepTime: 500,
  cursor: null,
  clearinfo: 0,

  poll: function() {
    var args = {"_xsrf": getCookie("_xsrf")};
    if(updater.cursor){
      args.cursor = updater.cursor;
    }
    updater.clearinfo = setTimeout(function(){
      updater.errorSleepTime = 500;
      showinfo(false);
    }, Math.min(3000, updater.errorSleepTime), false);

    $.ajax({
      url: "/a/message/updates",
      type: "POST",
      dataType: "json",
      data: $.param(args),
      error: updater.onError
    }).success(function(data){
      try{
	if(data.status == 'ok'){
	  updater.newMessages(data);
	  window.setTimeout(updater.poll, 0);
	}else if(data.status == 'try again'){
	  window.setTimeout(updater.poll, 0);
	}else{
	  console.warning('bad response', data);
	  updater.onError();
	}
      }catch(e){
	console.error(e);
	updater.onError();
	return;
      }
    });
  },

  onError: function(response) {
    if(updater.errorSleepTime < 60000){
      updater.errorSleepTime *= 2;
    }
    clearTimeout(updater.clearinfo);
    showinfo("network error; try again in " + (updater.errorSleepTime / 1000) +  "s");
    window.setTimeout(updater.poll, updater.errorSleepTime);
  },

  newMessages: function(response) {
    if(!response.messages){
      return;
    }
    updater.cursor = response.cursor;
    var messages = response.messages;
    updater.cursor = messages[messages.length - 1].id;
    console.log(messages.length, "new messages, cursor:", updater.cursor);
    for(var i = 0; i < messages.length; i++) {
      updater.showMessage(messages[i]);
    }
    if(info.focus === false){
      info.unread += messages.length;
      updateTitle();
    }
  },

  showMessage: function(message) {
    var shouldScroll = window.scrollY == getMaxY() && window.scrollY !== 0;
    var existing = $("#m" + message.id);
    if(existing.length > 0){
      return;
    }
    var node = $(message.html);
    $("#inbox").append(node);
    if(shouldScroll){
      scrollWindow();
    }
  }
};


var newMessage = function(form){
  if(/^\s*$/.test(form.find('[name=body]').val())){
    return false;
  }
  var message = form.formToDict();
  var disabled = form.find("input");
  disabled.disable();
  $.postJSON("/a/message/new", message, function(response) {
    updater.showMessage(response);
    form.find("input[type=text]").val("").focus();
    disabled.enable();
  });
};

$(document).ready(function() {
  if(!window.console){
    window.console = {};
    window.console.log = function() {};
    window.console.warning = function() {};
    window.console.error = function() {};
  }

  info = {
    originalTitle: document.title,
    unread: 0
  };

  $(window).bind("blur", function() {
    info.focus = false;
    updateTitle();
  }).bind('scroll', function() {
    if(getMaxY() - window.scrollY > 5){
      info.focus = false;
      updateTitle();
    }else{
      info.focus = true;
      info.unread = 0;
      updateTitle();
    }
  });

  $(window).bind("focus", function() {
    if(getMaxY() - window.scrollY <= 5){
      info.focus = true;
      info.unread = 0;
      updateTitle();
    }
  });

  $("#messageform").live("submit", function() {
    newMessage($(this));
    return false;
  });
  $("#messageform").live("keypress", function(e) {
    if (e.keyCode == 13) {
      newMessage($(this));
      return false;
    }
  });
  updater.poll();
  scrollWindow();
});

jQuery.postJSON = function(url, args, callback) {
  args._xsrf = getCookie("_xsrf");
  $.ajax({
    url: url,
    data: $.param(args),
    dataType: "json",
    type: "POST",
    success: function(response) {
      if(callback){
	callback(response);
      }
    }, error: function(response) {
      console.log("ERROR:", response);
    }
  });
};

jQuery.fn.formToDict = function() {
  var fields = this.serializeArray();
  var json = {};
  for (var i = 0; i < fields.length; i++) {
    json[fields[i].name] = fields[i].value;
  }
  if(json.next){
    delete json.next;
  }
  return json;
};

jQuery.fn.disable = function() {
  this.enable(false);
  return this;
};

jQuery.fn.enable = function(opt_enable) {
  if (arguments.length && !opt_enable) {
    this.attr("disabled", "disabled");
  } else {
    this.removeAttr("disabled");
  }
  return this;
};

