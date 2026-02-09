Ext.define('PVE.panel.AIChatPanel', {
	extend: 'Ext.panel.Panel',
	alias: 'widget.pveAIChatPanel',

	title: 'Proxision',
	iconCls: 'fa fa-comments',

	layout: {
		type: 'vbox',
		align: 'stretch',
	},

	scrollable: false,
	border: false,
	bodyPadding: 0,

	getProxyUrl: function () {
		return 'https://' + window.location.hostname + ':5555';
	},

	renderMarkdown: function (text) {
		let s = Ext.String.htmlEncode(text);

		// Fenced code blocks: ```lang\n...```
		s = s.replace(/```[\w]*\n?([\s\S]*?)```/g,
			'<pre class="pve-ai-code"><code>$1</code></pre>');

		// Inline code: `...`
		s = s.replace(/`([^`\n]+)`/g,
			'<code class="pve-ai-icode">$1</code>');

		// Bold: **...**
		s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

		// Italic: *...*
		s = s.replace(/\*([^*]+?)\*/g, '<em>$1</em>');

		// Headers
		s = s.replace(/(^|\n)### (.+)/g, '$1<strong style="font-size:1.05em">$2</strong>');
		s = s.replace(/(^|\n)## (.+)/g, '$1<strong style="font-size:1.1em">$2</strong>');
		s = s.replace(/(^|\n)# (.+)/g, '$1<strong style="font-size:1.15em">$2</strong>');

		// Markdown links: [title](url)
		s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g,
			'<a href="$2" target="_blank" rel="noopener" class="pve-ai-link">$1</a>');

		// Unordered list items
		s = s.replace(/(^|\n)- (.+)/g, '$1\u2022 $2');

		// Newlines to <br>, but not inside <pre> blocks
		let parts = s.split(/(<pre class="pve-ai-code">[\s\S]*?<\/pre>)/g);
		for (let i = 0; i < parts.length; i++) {
			if (parts[i].indexOf('<pre class="pve-ai-code">') !== 0) {
				parts[i] = parts[i].replace(/\n/g, '<br>');
			}
		}
		s = parts.join('');

		return s;
	},

	addBubble: function (role, text) {
		let me = this;
		let messageArea = me.down('#chatMessages');
		let isUser = role === 'user';

		let rendered = isUser
			? Ext.String.htmlEncode(text).replace(/\n/g, '<br>')
			: me.renderMarkdown(text);

		let cls = isUser ? 'pve-ai-bubble-user' : 'pve-ai-bubble-assistant';
		let icon = isUser ? 'fa fa-user' : 'fa fa-comments';
		let label = isUser ? 'You' : 'Proxision';

		messageArea.add({
			xtype: 'component',
			cls: 'pve-ai-bubble-wrap',
			html: '<div class="pve-ai-bubble ' + cls + '">' +
				'<div class="pve-ai-bubble-header">' +
				'<i class="' + icon + '"></i> ' + label +
				'</div>' +
				'<div class="pve-ai-bubble-body">' + rendered + '</div>' +
				'</div>',
		});

		Ext.defer(function () {
			messageArea.updateLayout();
			let scroller = messageArea.getScrollable();
			if (scroller) {
				scroller.scrollTo(0, scroller.getMaxPosition().y);
			}
		}, 100);
		// Safety net for long messages that take longer to render
		Ext.defer(function () {
			let scroller = messageArea.getScrollable();
			if (scroller) {
				scroller.scrollTo(0, scroller.getMaxPosition().y);
			}
		}, 400);
	},

	_setProcessing: function (active) {
		let me = this;
		let btn = me.down('#sendStopBtn');
		let input = me.down('#chatInput');
		if (active) {
			me._processing = true;
			btn.setIconCls('fa fa-stop');
			btn.setText('Stop');
			btn.removeCls('pve-ai-send-btn');
			btn.addCls('pve-ai-stop-btn');
			input.setDisabled(true);
		} else {
			me._processing = false;
			me._abortController = null;
			btn.setIconCls('fa fa-paper-plane');
			btn.setText('Send');
			btn.removeCls('pve-ai-stop-btn');
			btn.addCls('pve-ai-send-btn');
			input.setDisabled(false);
			input.focus(false, 100);
		}
	},

	_handleShellEvent: function (data) {
		let me = this;
		let vmid = data.vmid;
		if (!me._shellEntries) me._shellEntries = {};
		if (!me._shellNodes) me._shellNodes = {};
		if (!me._shellEntries[vmid]) me._shellEntries[vmid] = [];
		if (data.node) me._shellNodes[vmid] = data.node;
		let entries = me._shellEntries[vmid];

		if (data.type === 'shell_start') {
			entries.push({
				command: data.command || '',
				output: '',
				exit_code: null,
				running: true,
			});
			me._doShellRender(vmid);
		} else if (data.type === 'shell_output') {
			if (entries.length > 0) {
				entries[entries.length - 1].output += (data.output || '');
			}
			if (!me._shellOutputTimer) {
				me._shellOutputTimer = Ext.defer(function () {
					me._shellOutputTimer = null;
					me._doShellRender(vmid);
				}, 100);
			}
		} else if (data.type === 'shell_end') {
			if (me._shellOutputTimer) {
				clearTimeout(me._shellOutputTimer);
				me._shellOutputTimer = null;
			}
			if (entries.length > 0) {
				entries[entries.length - 1].exit_code = data.exit_code;
				entries[entries.length - 1].running = false;
			}
			me._doShellRender(vmid);
		}
	},

	_doShellRender: function (vmid) {
		let me = this;
		let messageArea = me.down('#chatMessages');
		let shellId = 'ai-shell-' + vmid;
		let shellComp = messageArea.down('#' + shellId);
		let bodyHtml = me._renderShellEntries(vmid);

		if (shellComp && shellComp.el) {
			let bodyEl = shellComp.el.down('.pve-ai-shell-body');
			if (bodyEl && bodyEl.dom) {
				bodyEl.dom.innerHTML = bodyHtml;
				bodyEl.dom.scrollTop = bodyEl.dom.scrollHeight;
			}
			let scroller = messageArea.getScrollable();
			if (scroller) {
				scroller.scrollTo(0, scroller.getMaxPosition().y);
			}
		} else {
			let node = (me._shellNodes && me._shellNodes[vmid]) || '';
			let fullHtml = '<div class="pve-ai-bubble pve-ai-bubble-assistant">' +
				'<div class="pve-ai-bubble-header">' +
				'<i class="fa fa-terminal"></i> CT ' + vmid + ' \u2014 Shell' +
				'<span class="pve-ai-shell-open" data-vmid="' + vmid + '" data-node="' + Ext.String.htmlEncode(node) + '">Open Shell <i class="fa fa-external-link"></i></span>' +
				'</div>' +
				'<div class="pve-ai-bubble-body">' +
				'<div class="pve-ai-shell-preview">' +
				'<div class="pve-ai-shell-body">' + bodyHtml + '</div>' +
				'</div>' +
				'</div>' +
				'</div>';
			messageArea.add({
				xtype: 'component',
				itemId: shellId,
				cls: 'pve-ai-bubble-wrap',
				html: fullHtml,
			});
			Ext.defer(function () {
				messageArea.updateLayout();
				let scroller = messageArea.getScrollable();
				if (scroller) {
					scroller.scrollTo(0, scroller.getMaxPosition().y);
				}
			}, 50);
		}
	},

	_renderShellEntries: function (vmid) {
		let me = this;
		let entries = (me._shellEntries && me._shellEntries[vmid]) || [];
		let html = '';

		for (let i = 0; i < entries.length; i++) {
			let d = entries[i];
			let cmd = d.command || '';
			let cmdDisplay = cmd.length > 200 ? cmd.substring(0, 200) + '...' : cmd;

			let output = d.output || '';
			let outputLines = output.split('\n');
			while (outputLines.length > 0 && outputLines[outputLines.length - 1].trim() === '') {
				outputLines.pop();
			}

			let maxLines = 8;
			let truncated = outputLines.length > maxLines;
			if (truncated) {
				outputLines = outputLines.slice(-maxLines);
			}

			html += '<div class="pve-ai-shell-entry">';
			html += '<div class="pve-ai-shell-prompt-line">';
			html += '<span class="pve-ai-shell-prompt">root@CT' + vmid + ':~#</span> ';
			html += '<span class="pve-ai-shell-cmd">' + Ext.String.htmlEncode(cmdDisplay) + '</span></div>';

			if (truncated) {
				html += '<div class="pve-ai-shell-truncated">\u2191 earlier output hidden</div>';
			}
			if (outputLines.length > 0 && outputLines.join('').trim()) {
				html += '<div class="pve-ai-shell-output">' + Ext.String.htmlEncode(outputLines.join('\n')) + '</div>';
			}
			if (d.running) {
				html += '<span class="pve-ai-shell-cursor">\u2588</span>';
			}
			if (!d.running && d.exit_code !== null && d.exit_code !== undefined && d.exit_code !== 0) {
				html += '<div class="pve-ai-shell-exit-err"><i class="fa fa-times-circle"></i> exit code ' + d.exit_code + '</div>';
			}
			html += '</div>';
		}

		return html;
	},

	_executeAction: function (action, params, retries) {
		let me = this;
		if (retries === undefined) retries = 3;
		return fetch(me.getProxyUrl() + '/execute', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ action: action, params: params }),
		}).then(function (resp) {
			return resp.json();
		}).catch(function (err) {
			if (retries > 1) {
				return new Promise(function (resolve) {
					setTimeout(resolve, 1500);
				}).then(function () {
					return me._executeAction(action, params, retries - 1);
				});
			}
			throw err;
		});
	},

	_stopAndCleanup: function () {
		let me = this;
		if (!me._processing) return;

		if (me._abortController) {
			me._abortController.abort();
		}

		let messageArea = me.down('#chatMessages');
		if (me._shellOutputTimer) {
			clearTimeout(me._shellOutputTimer);
			me._shellOutputTimer = null;
		}
		if (me._shellEntries) {
			for (let vid in me._shellEntries) {
				let sc = messageArea.down('#ai-shell-' + vid);
				if (sc) messageArea.remove(sc);
			}
			me._shellEntries = {};
			me._shellNodes = {};
		}
		let loadEl = me._currentLoadingId ? messageArea.down('#' + me._currentLoadingId) : null;
		if (loadEl) {
			messageArea.remove(loadEl);
		}
		me._setProcessing(false);

		let created = me._createdResources || [];
		if (created.length === 0) {
			me.addBubble('assistant', 'Task stopped.');
			return;
		}

		let last = created[created.length - 1];
		let typeLabel = last.type === 'ct' ? 'container' : 'VM';
		let vmid = last.vmid;

		Ext.Msg.show({
			title: 'Task Stopped',
			message: 'A ' + typeLabel + ' (ID: ' + vmid + ') was created during this task.<br><br>' +
				'Would you like to <b>delete</b> it or <b>keep</b> it?',
			buttons: Ext.Msg.YESNO,
			buttonText: { yes: 'Delete', no: 'Keep' },
			icon: Ext.Msg.QUESTION,
			fn: function (btnId) {
				if (btnId === 'yes') {
					let delLoadId = 'ai-del-' + Ext.id();
					messageArea.add({
						xtype: 'component',
						itemId: delLoadId,
						cls: 'pve-ai-bubble-wrap',
						html: '<div class="pve-ai-bubble pve-ai-bubble-assistant">' +
							'<div class="pve-ai-bubble-header">' +
							'<i class="fa fa-comments"></i> Proxision' +
							'</div>' +
							'<div class="pve-ai-bubble-body pve-ai-loading-body">' +
							'<i class="fa fa-spinner fa-spin"></i> Deleting ' + typeLabel + ' ' + vmid + '...' +
							'</div>' +
							'</div>',
					});
					Ext.defer(function () {
						messageArea.updateLayout();
						let scroller = messageArea.getScrollable();
						if (scroller) {
							scroller.scrollTo(0, scroller.getMaxPosition().y);
						}
					}, 100);
					let stopAction = last.type === 'ct' ? 'stop_container' : 'stop_vm';
					let deleteAction = last.type === 'ct' ? 'delete_container' : 'delete_vm';
					me._executeAction(stopAction, { vmid: vmid })
						.then(function () {
							return me._executeAction(deleteAction, { vmid: vmid });
						})
						.then(function (res) {
							let delEl = messageArea.down('#' + delLoadId);
							if (delEl) {
								messageArea.remove(delEl);
							}
							if (res && res.success) {
								me.addBubble('assistant', typeLabel.charAt(0).toUpperCase() + typeLabel.slice(1) + ' ' + vmid + ' deleted successfully.');
							} else {
								me.addBubble('assistant', 'Could not delete ' + typeLabel + ' ' + vmid + ': ' + (res.error || 'Unknown error'));
							}
						})
						.catch(function (err) {
							let delEl = messageArea.down('#' + delLoadId);
							if (delEl) {
								messageArea.remove(delEl);
							}
							me.addBubble('assistant', 'Error deleting ' + typeLabel + ': ' + String(err));
						});
				} else {
					me.addBubble('assistant', 'Task stopped. ' + typeLabel.charAt(0).toUpperCase() + typeLabel.slice(1) + ' ' + vmid + ' was kept.');
				}
			},
		});
	},

	sendMessage: function () {
		let me = this;

		if (me._processing) {
			me._stopAndCleanup();
			return;
		}

		let input = me.down('#chatInput');
		let text = (input.getValue() || '').trim();
		if (!text) {
			return;
		}

		let model = localStorage.getItem('pve-ai-model');
		let apiKey = model ? localStorage.getItem('pve-ai-apikey-' + model) : null;

		if (!model || !apiKey) {
			Ext.Msg.alert('No Model Configured',
				'Please click "Set Model" to select an AI model and enter your API key.');
			return;
		}

		me.chatHistory.push({ role: 'user', content: text });
		me.addBubble('user', text);
		input.setValue('');

		let welcome = me.down('#welcomeBox');
		if (welcome) {
			welcome.setHidden(true);
		}

		me._createdResources = [];
		me._shellEntries = {};
		me._shellNodes = {};
		if (me._shellOutputTimer) {
			clearTimeout(me._shellOutputTimer);
			me._shellOutputTimer = null;
		}
		let loadingId = 'ai-loading-' + Ext.id();
		me._currentLoadingId = loadingId;
		let messageArea = me.down('#chatMessages');
		messageArea.add({
			xtype: 'component',
			itemId: loadingId,
			cls: 'pve-ai-bubble-wrap',
			html: '<div class="pve-ai-bubble pve-ai-bubble-assistant">' +
				'<div class="pve-ai-bubble-header">' +
				'<i class="fa fa-comments"></i> Proxision' +
				'</div>' +
				'<div class="pve-ai-bubble-body pve-ai-loading-body">' +
				'<i class="fa fa-spinner fa-spin"></i> Thinking...' +
				'</div>' +
				'</div>',
		});

		Ext.defer(function () {
			messageArea.updateLayout();
			let scroller = messageArea.getScrollable();
			if (scroller) {
				scroller.scrollTo(0, scroller.getMaxPosition().y);
			}
		}, 100);

		me._setProcessing(true);

		let abortController = new AbortController();
		me._abortController = abortController;

		let allMessages = [{
			role: 'system',
			content: 'You are Proxision, an AI assistant integrated into Proxmox Virtual Environment. ' +
				'You help users manage their virtualization infrastructure including VMs, ' +
				'containers, storage, and networking. Be concise and helpful.',
		}].concat(me.chatHistory);

		fetch(me.getProxyUrl() + '/chat', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({
				model: model,
				api_key: apiKey,
				messages: allMessages,
			}),
			signal: abortController.signal,
		})
			.then(function (resp) {
				let reader = resp.body.getReader();
				let decoder = new TextDecoder();
				let buffer = '';

				function processStream() {
					return reader.read().then(function (result) {
						if (result.done) {
							let leftover = messageArea.down('#' + loadingId);
							if (leftover) {
								messageArea.remove(leftover);
								me.addBubble('assistant', 'No response received.');
							}
							me._setProcessing(false);
							return;
						}

						buffer += decoder.decode(result.value, { stream: true });
						let lines = buffer.split('\n');
						buffer = lines.pop();

						for (let i = 0; i < lines.length; i++) {
							let line = lines[i].trim();
							if (!line) continue;

							try {
								let data = JSON.parse(line);

								if (data.type === 'status') {
									let loadEl = messageArea.down('#' + loadingId);
									if (loadEl && loadEl.el) {
										let bodyEl = loadEl.el.down('.pve-ai-bubble-body');
										if (bodyEl) {
											let msg = data.message || '';
											if (msg.length > 70) {
												msg = msg.substring(0, 70) + '...';
											}
											bodyEl.setHtml(
												'<i class="fa fa-spinner fa-spin"></i> ' +
												Ext.String.htmlEncode(msg),
											);
										}
									}
									if (data.created_vmid) {
										me._createdResources.push({
											vmid: data.created_vmid,
											type: data.created_type || 'ct',
										});
									}
									Ext.defer(function () {
										messageArea.updateLayout();
										let scroller = messageArea.getScrollable();
										if (scroller) {
											scroller.scrollTo(0, scroller.getMaxPosition().y);
										}
									}, 100);
								} else if (data.type === 'shell_start' || data.type === 'shell_output' || data.type === 'shell_end') {
									me._handleShellEvent(data);
								} else if (data.type === 'done') {
									if (me._shellEntries) {
										for (let vid in me._shellEntries) {
											let sc = messageArea.down('#ai-shell-' + vid);
											if (sc) messageArea.remove(sc);
										}
										me._shellEntries = {};
										me._shellNodes = {};
									}
									let loadEl = messageArea.down('#' + loadingId);
									if (loadEl) {
										messageArea.remove(loadEl);
									}
									let reply = data.response || 'No response received.';
									me.chatHistory.push({ role: 'assistant', content: reply });
									me.addBubble('assistant', reply);
									me._setProcessing(false);
								} else if (data.type === 'error') {
									let loadEl = messageArea.down('#' + loadingId);
									if (loadEl) {
										messageArea.remove(loadEl);
									}
									me.addBubble('assistant', 'Error: ' + data.error);
									me._setProcessing(false);
								}
							} catch (e) {
								// ignore malformed lines
							}
						}

						return processStream();
					});
				}

				return processStream();
			})
			.catch(function (err) {
				if (err.name === 'AbortError') {
					return;
				}
				let loading = messageArea.down('#' + loadingId);
				if (loading) {
					messageArea.remove(loading);
				}
				me.addBubble('assistant',
					'Connection error. Make sure the AI proxy is running.\n' +
					'Try visiting https://' + window.location.hostname + ':5555/ ' +
					'in a new tab to accept the certificate.\n\n' + String(err));
				me._setProcessing(false);
			});
	},

	clearChat: function () {
		let me = this;
		if (me._processing) {
			if (me._abortController) {
				me._abortController.abort();
			}
			me._setProcessing(false);
		}
		me.chatHistory = [];
		me._createdResources = [];
		me._shellEntries = {};
		me._shellNodes = {};
		if (me._shellOutputTimer) {
			clearTimeout(me._shellOutputTimer);
			me._shellOutputTimer = null;
		}
		let messageArea = me.down('#chatMessages');
		messageArea.removeAll();
		let welcome = me.down('#welcomeBox');
		if (welcome) {
			welcome.setHidden(false);
		}
	},

	initComponent: function () {
		let me = this;
		me.chatHistory = [];

		let messageArea = Ext.create('Ext.container.Container', {
			itemId: 'chatMessages',
			flex: 1,
			scrollable: {
				y: 'scroll',
				x: false,
			},
			cls: 'pve-ai-chat-messages',
			style: {
				paddingBottom: '6px',
			},
			layout: {
				type: 'vbox',
				align: 'stretch',
			},
			listeners: {
				afterrender: function (comp) {
					comp.el.on('click', function (e) {
						let el = e.getTarget('.pve-ai-shell-open', 5, true);
						if (el) {
							let vmid = el.getAttribute('data-vmid');
							let node = el.getAttribute('data-node');
							if (vmid && node) {
								PVE.Utils.openDefaultConsoleWindow(true, 'lxc', vmid, node, 'CT' + vmid);
							}
						}
					});
				},
			},
			items: [
				{
					xtype: 'container',
					itemId: 'welcomeBox',
					cls: 'pve-ai-chat-welcome',
					padding: '20 25 20 15',
					html: '<div class="pve-ai-chat-welcome-inner">' +
						'<div class="pve-ai-chat-welcome-icon">' +
						'<i class="fa fa-comments fa-3x"></i>' +
						'</div>' +
						'<h2>Proxision</h2>' +
						'<p>Ask me anything about your Proxmox environment. ' +
						'I can help you manage VMs, containers, storage, and more.</p>' +
						'</div>',
				},
			],
		});

		let inputField = Ext.create('Ext.form.field.TextArea', {
			itemId: 'chatInput',
			emptyText: 'Ask the AI assistant...',
			enableKeyEvents: true,
			grow: true,
			growMin: 60,
			growMax: 120,
			cls: 'pve-ai-chat-input',
			listeners: {
				keydown: function (field, e) {
					if (e.getKey() === e.ENTER && !e.shiftKey) {
						e.preventDefault();
						me.sendMessage();
					}
				},
			},
		});

		let addButton = Ext.createWidget('button', {
			baseCls: 'x-btn',
			iconCls: 'fa fa-plus',
			tooltip: 'Attach',
		});

		let modelButton = Ext.createWidget('button', {
			baseCls: 'x-btn',
			iconCls: 'fa fa-cogs',
			text: 'Set Model',
			handler: function () {
				Ext.create('PVE.window.AIModelSettings', {
					autoShow: true,
				});
			},
		});

		let sendButton = Ext.createWidget('button', {
			itemId: 'sendStopBtn',
			baseCls: 'x-btn',
			cls: 'pve-ai-send-btn',
			iconCls: 'fa fa-paper-plane',
			text: 'Send',
			handler: function () {
				me.sendMessage();
			},
		});

		Ext.apply(me, {
			items: [
				messageArea,
				{
					xtype: 'toolbar',
					height: 1,
					padding: 0,
					margin: 0,
					items: [],
				},
				{
					xtype: 'panel',
					border: false,
					bodyPadding: '8 10 10 10',
					bodyCls: 'pve-ai-chat-input-bar',
					header: false,
					layout: {
						type: 'vbox',
						align: 'stretch',
					},
					items: [
						inputField,
						{
							xtype: 'container',
							baseCls: 'x-plain',
							border: false,
							margin: '5 0 0 0',
							layout: {
								type: 'hbox',
								align: 'middle',
							},
							items: [
								addButton,
								{
									xtype: 'component',
									width: 5,
								},
								modelButton,
								{
									xtype: 'component',
									flex: 1,
								},
								sendButton,
							],
						},
					],
				},
			],
			tools: [],
		});

		me.callParent();
	},
});
