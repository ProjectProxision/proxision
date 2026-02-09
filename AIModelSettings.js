Ext.define('PVE.window.AIModelSettings', {
    extend: 'Ext.window.Window',

    title: 'AI Model Settings',
    iconCls: 'fa fa-cogs',
    width: 400,
    modal: true,
    resizable: false,
    border: false,

    initComponent: function () {
	let me = this;

	let modelStore = Ext.create('Ext.data.ArrayStore', {
	    fields: ['id', 'name'],
	    data: [
		['gpt-5.2', 'GPT 5.2'],
		['gemini-3-flash', 'Gemini 3 Flash'],
		['grok-4.1-fast', 'Grok 4.1 Fast'],
	    ],
	});

	let savedModel = localStorage.getItem('pve-ai-model') || '';

	let modelCombo = Ext.create('Ext.form.field.ComboBox', {
	    fieldLabel: 'AI Model',
	    name: 'model',
	    itemId: 'modelCombo',
	    store: modelStore,
	    queryMode: 'local',
	    displayField: 'name',
	    valueField: 'id',
	    editable: false,
	    forceSelection: true,
	    value: savedModel || undefined,
	    emptyText: 'Select a model...',
	    anchor: '100%',
	});

	let apiKeyField = Ext.create('Ext.form.field.Text', {
	    fieldLabel: 'API Key',
	    name: 'apiKey',
	    itemId: 'apiKeyField',
	    inputType: 'password',
	    emptyText: 'Enter your API key...',
	    anchor: '100%',
	});

	let statusBox = Ext.create('Ext.Component', {
	    itemId: 'statusBox',
	    hidden: true,
	    padding: '8 0 0 0',
	    html: '',
	});

	modelCombo.on('change', function (field, newVal) {
	    if (newVal) {
		let saved = localStorage.getItem('pve-ai-apikey-' + newVal) || '';
		apiKeyField.setValue(saved);
		if (saved) {
		    statusBox.setHidden(false);
		    statusBox.update('<i class="fa fa-check" style="color:#21bf4b;"></i> API key loaded for this model');
		} else {
		    statusBox.setHidden(false);
		    statusBox.update('<i class="fa fa-info-circle" style="color:#3892d4;"></i> Enter an API key for this model');
		}
	    }
	});

	if (savedModel) {
	    let saved = localStorage.getItem('pve-ai-apikey-' + savedModel) || '';
	    apiKeyField.setValue(saved);
	}

	Ext.apply(me, {
	    bodyPadding: 15,
	    layout: 'anchor',
	    defaults: {
		anchor: '100%',
		labelWidth: 80,
	    },
	    items: [
		modelCombo,
		apiKeyField,
		statusBox,
	    ],
	    buttons: [
		{
		    text: 'Save',
		    iconCls: 'fa fa-check',
		    handler: function () {
			let model = modelCombo.getValue();
			let apiKey = apiKeyField.getValue();

			if (!model) {
			    Ext.Msg.alert('Error', 'Please select an AI model.');
			    return;
			}

			if (!apiKey) {
			    Ext.Msg.alert('Error', 'Please enter an API key.');
			    return;
			}

			localStorage.setItem('pve-ai-model', model);
			localStorage.setItem('pve-ai-apikey-' + model, apiKey);
			me.close();
		    },
		},
		{
		    text: 'Cancel',
		    handler: function () {
			me.close();
		    },
		},
	    ],
	});

	me.callParent();
    },
});
