/**
 * NinjaOne-Jira Integration Setup - Application Logic
 * Supports role-based object type mappings
 */

// State management
const state = {
    ninjaConnected: false,
    jiraConnected: false,
    workspaceId: null,
    schemas: [],
    objectTypes: [],
    roles: [],
    objectTypeMappings: [], // Array of {roleId, roleName, objectTypeId, objectTypeName, ninjaDeviceIdAttrId, attributeMappings[]}
    jiraProjects: [],
    jiraIssueTypes: [],
    jiraIssueFields: [],
    alertsTabLoaded: false,
    issueConfig: {
        projectKey: '',
        projectName: '',
        issueTypeId: '',
        issueTypeName: '',
        summaryTemplate: '[NinjaOne] {severity}: {device_name} - {message}',
        minSeverity: '',
        sourceTypes: [],
        alertFieldMappings: [],
        jsmFieldMappings: [],
        assetFieldId: '',
        resolveTargetStatus: '',
        resolveComment: '',
        retriggerBehavior: 'new_issue',
        reopenTargetStatus: '',
        reopenComment: '',
    },
    notifConfig: {
        enabled: false,
        url: '',
        token: '',
        intervalSeconds: 60,
        notifyOnChanges: true,
    },
};

// Tab navigation
document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
        const tabId = tab.dataset.tab;
        switchTab(tabId);
    });
});

function switchTab(tabId) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));

    document.querySelector(`[data-tab="${tabId}"]`).classList.add('active');
    document.getElementById(`tab-${tabId}`).classList.add('active');

    if (tabId === 'review') {
        updateConfigPreview();
    } else if (tabId === 'alerts') {
        loadAlertsTabData();
    }
}

function nextTab(tabId) {
    switchTab(tabId);
}

function prevTab(tabId) {
    switchTab(tabId);
}

// API helpers
async function api(method, path, body = null) {
    const options = {
        method,
        headers: { 'Content-Type': 'application/json' },
    };
    if (body) {
        options.body = JSON.stringify(body);
    }
    const response = await fetch(path, options);
    const data = await response.json();

    // Check for HTTP errors
    if (!response.ok) {
        let msg = data.detail || data.message || `HTTP ${response.status}`;
        if (typeof msg === 'object') {
            msg = JSON.stringify(msg);
        }
        throw new Error(msg);
    }
    return data;
}

function getCredentials() {
    return {
        ninja_base_url: document.getElementById('ninja-url').value,
        ninja_client_id: document.getElementById('ninja-client-id').value,
        ninja_client_secret: document.getElementById('ninja-client-secret').value,
        jira_subdomain: document.getElementById('jira-subdomain').value,
        jira_email: document.getElementById('jira-email').value,
        jira_api_token: document.getElementById('jira-token').value,
    };
}

function getJiraParams() {
    const creds = getCredentials();
    return new URLSearchParams({
        subdomain: creds.jira_subdomain,
        email: creds.jira_email,
        api_token: creds.jira_api_token,
    });
}

function getNinjaParams() {
    const creds = getCredentials();
    return new URLSearchParams({
        base_url: creds.ninja_base_url,
        client_id: creds.ninja_client_id,
        client_secret: creds.ninja_client_secret,
    });
}

// Status display
function showStatus(elementId, status, message) {
    const el = document.getElementById(elementId);
    if (!el) return;
    el.className = `status visible ${status}`;
    el.textContent = message;
}

function hideStatus(elementId) {
    const el = document.getElementById(elementId);
    if (!el) return;
    el.className = 'status';
}

function setSpinner(elementId, active) {
    const el = document.getElementById(elementId);
    if (el) {
        el.classList.toggle('active', active);
    }
}

// Connection tests
async function testNinjaOne() {
    const creds = getCredentials();

    if (!creds.ninja_client_id || !creds.ninja_client_secret) {
        showStatus('ninja-status', 'error', 'Please enter Client ID and Client Secret');
        return;
    }

    setSpinner('ninja-spinner', true);
    showStatus('ninja-status', 'loading', 'Testing connection...');

    try {
        const result = await api('POST', '/api/test/ninjaone', creds);

        if (result.status === 'success') {
            state.ninjaConnected = true;
            showStatus('ninja-status', 'success', result.message);

            // Load roles
            await loadRoles();
        } else {
            showStatus('ninja-status', 'error', result.message);
        }
    } catch (e) {
        showStatus('ninja-status', 'error', 'Connection failed: ' + e.message);
    } finally {
        setSpinner('ninja-spinner', false);
    }
}

async function testJira() {
    const creds = getCredentials();

    if (!creds.jira_subdomain || !creds.jira_email || !creds.jira_api_token) {
        showStatus('jira-status', 'error', 'Please enter all Jira credentials');
        return;
    }

    setSpinner('jira-spinner', true);
    showStatus('jira-status', 'loading', 'Testing connection...');

    try {
        const result = await api('POST', '/api/test/jira', creds);

        if (result.status === 'success') {
            state.jiraConnected = true;
            state.workspaceId = result.workspace_id;
            showStatus('jira-status', 'success', `${result.message} (Workspace: ${result.workspace_id})`);

            // Load schemas
            await loadSchemas();

            // Reset alerts tab so it reloads with fresh credentials
            state.alertsTabLoaded = false;
        } else {
            showStatus('jira-status', 'error', result.message);
        }
    } catch (e) {
        showStatus('jira-status', 'error', 'Connection failed: ' + e.message);
    } finally {
        setSpinner('jira-spinner', false);
    }
}

// Load NinjaOne roles
async function loadRoles() {
    try {
        const result = await fetch('/api/ninjaone/roles?' + getNinjaParams());
        const data = await result.json();
        state.roles = data.roles || [];
        console.log('Loaded roles:', state.roles);
    } catch (e) {
        console.error('Failed to load roles:', e);
    }
}

// Schema and type loading
async function loadSchemas() {
    const select = document.getElementById('jira-schema');
    select.innerHTML = '<option value="">Loading...</option>';

    try {
        const result = await fetch('/api/jira/schemas?' + getJiraParams());
        const data = await result.json();

        state.schemas = data.schemas || [];

        select.innerHTML = '<option value="">-- Select Schema --</option>';
        for (const schema of state.schemas) {
            select.innerHTML += `<option value="${schema.id}">${schema.name}</option>`;
        }
    } catch (e) {
        console.error('Failed to load schemas:', e);
        select.innerHTML = '<option value="">-- Failed to load --</option>';
    }
}

async function loadObjectTypes() {
    const schemaId = document.getElementById('jira-schema').value;
    const preview = document.getElementById('schema-types-preview');

    if (!schemaId) {
        preview.innerHTML = '';
        state.objectTypes = [];
        return;
    }

    preview.innerHTML = '<p style="color: var(--text-muted);">Loading object types...</p>';

    try {
        const result = await fetch(`/api/jira/types/${schemaId}?` + getJiraParams());
        const data = await result.json();

        state.objectTypes = data.types || [];

        let html = '<table><thead><tr><th>Object Type</th><th>ID</th></tr></thead><tbody>';
        for (const type of state.objectTypes) {
            html += `<tr><td>${type.name}</td><td>${type.id}</td></tr>`;
        }
        html += '</tbody></table>';
        preview.innerHTML = html;
    } catch (e) {
        console.error('Failed to load types:', e);
        preview.innerHTML = '<p style="color: var(--error);">Failed to load object types</p>';
    }
}

// Object Type Mapping Management
function addObjectTypeMapping() {
    const index = state.objectTypeMappings.length;
    state.objectTypeMappings.push({
        roleId: null,
        roleName: '',
        objectTypeId: '',
        objectTypeName: '',
        ninjaDeviceIdAttrId: '',
        attributes: [],
        attributeMappings: [],
    });
    renderObjectTypeMappings();
}

function removeObjectTypeMapping(button) {
    const card = button.closest('.mapping-card');
    const index = parseInt(card.dataset.mappingIndex);
    state.objectTypeMappings.splice(index, 1);
    renderObjectTypeMappings();
}

function renderObjectTypeMappings() {
    const container = document.getElementById('object-type-mappings-container');
    container.innerHTML = '';

    if (state.objectTypeMappings.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-state-icon">📦</div>
                <div class="empty-state-text">No role mappings configured yet.<br>Add a mapping to sync devices to Jira Assets.</div>
            </div>
        `;
        return;
    }

    const template = document.getElementById('role-mapping-template');

    state.objectTypeMappings.forEach((mapping, index) => {
        const clone = template.content.cloneNode(true);
        const card = clone.querySelector('.mapping-card');
        card.dataset.mappingIndex = index;

        // Update title
        const title = card.querySelector('.role-name');
        title.textContent = mapping.roleName || 'New Mapping';

        const badge = card.querySelector('.badge');
        if (mapping.roleId && mapping.objectTypeId) {
            badge.className = 'badge badge-success';
            badge.textContent = 'Configured';
        }

        // Populate role dropdown
        const roleSelect = card.querySelector('.role-select');
        roleSelect.innerHTML = '<option value="">-- Select Role --</option>';
        for (const role of state.roles) {
            const selected = mapping.roleId === role.id ? 'selected' : '';
            roleSelect.innerHTML += `<option value="${role.id}" ${selected}>${role.name}</option>`;
        }

        // Populate object type dropdown
        const typeSelect = card.querySelector('.object-type-select');
        typeSelect.innerHTML = '<option value="">-- Select Object Type --</option>';
        for (const type of state.objectTypes) {
            const selected = mapping.objectTypeId === type.id ? 'selected' : '';
            typeSelect.innerHTML += `<option value="${type.id}" ${selected}>${type.name}</option>`;
        }


        // Render attribute mappings
        const attrList = card.querySelector('.attribute-mappings-list');
        attrList.innerHTML = '';

        if (mapping.attributeMappings && mapping.attributeMappings.length > 0) {
            mapping.attributeMappings.forEach((attrMapping, attrIndex) => {
                const row = createAttributeMappingRow(index, attrIndex, attrMapping, mapping.attributes || []);
                attrList.appendChild(row);
            });
        }

        container.appendChild(clone);
    });
}

function createAttributeMappingRow(mappingIndex, attrIndex, attrMapping, availableAttrs) {
    const template = document.getElementById('attribute-mapping-template');
    const clone = template.content.cloneNode(true);
    const row = clone.querySelector('.mapping-row');
    row.dataset.mappingIndex = mappingIndex;
    row.dataset.attrIndex = attrIndex;

    // Populate Jira attribute dropdown
    const jiraSelect = row.querySelector('.jira-attr-select');

    if (availableAttrs.length === 0) {
        // No attributes loaded yet - disable dropdown
        jiraSelect.innerHTML = '<option value="">Select Object Type first</option>';
        jiraSelect.disabled = true;
    } else {
        jiraSelect.disabled = false;
        jiraSelect.innerHTML = '<option value="">-- Jira Attribute --</option>';
        for (const attr of availableAttrs) {
            const selected = attrMapping.jiraAttributeId === attr.id ? 'selected' : '';
            jiraSelect.innerHTML += `<option value="${attr.id}" ${selected}>${attr.name}</option>`;
        }
    }
    jiraSelect.onchange = () => updateAttributeMapping(mappingIndex, attrIndex, 'jiraAttributeId', jiraSelect.value, jiraSelect);

    // Set NinjaOne field selection
    const ninjaSelect = row.querySelector('.ninja-field-select');
    ninjaSelect.value = attrMapping.source || '';
    ninjaSelect.onchange = () => updateAttributeMapping(mappingIndex, attrIndex, 'source', ninjaSelect.value);

    // Set transform selection
    const transformSelect = row.querySelector('.transform-select');
    transformSelect.value = attrMapping.transform || '';
    transformSelect.onchange = () => updateAttributeMapping(mappingIndex, attrIndex, 'transform', transformSelect.value);

    // Set identity order
    const identityInput = row.querySelector('.identity-order-input');
    identityInput.value = attrMapping.identityOrder || '';
    identityInput.onchange = () => {
        const val = identityInput.value ? parseInt(identityInput.value) : null;
        updateAttributeMapping(mappingIndex, attrIndex, 'identityOrder', val);
    };

    return row;
}

function addAttributeMapping(button) {
    const card = button.closest('.mapping-card');
    const index = parseInt(card.dataset.mappingIndex);

    if (!state.objectTypeMappings[index].attributeMappings) {
        state.objectTypeMappings[index].attributeMappings = [];
    }

    state.objectTypeMappings[index].attributeMappings.push({
        jiraAttributeId: '',
        jiraAttributeName: '',
        source: '',
        transform: '',
        identityOrder: null,
    });

    renderObjectTypeMappings();
}

function removeAttributeMapping(button) {
    const row = button.closest('.mapping-row');
    const mappingIndex = parseInt(row.dataset.mappingIndex);
    const attrIndex = parseInt(row.dataset.attrIndex);

    state.objectTypeMappings[mappingIndex].attributeMappings.splice(attrIndex, 1);
    renderObjectTypeMappings();
}

function updateAttributeMapping(mappingIndex, attrIndex, field, value, selectElement = null) {
    const mapping = state.objectTypeMappings[mappingIndex].attributeMappings[attrIndex];
    mapping[field] = value;

    // For jira attribute, also store the name
    if (field === 'jiraAttributeId' && selectElement) {
        const option = selectElement.options[selectElement.selectedIndex];
        mapping.jiraAttributeName = option ? option.text : '';
    }
}

async function onRoleChange(select) {
    const card = select.closest('.mapping-card');
    const index = parseInt(card.dataset.mappingIndex);
    const roleId = parseInt(select.value);

    const role = state.roles.find(r => r.id === roleId);
    state.objectTypeMappings[index].roleId = roleId || null;
    state.objectTypeMappings[index].roleName = role ? role.name : '';

    renderObjectTypeMappings();
}

async function onObjectTypeChange(select) {
    const card = select.closest('.mapping-card');
    const index = parseInt(card.dataset.mappingIndex);
    const typeId = select.value;

    const type = state.objectTypes.find(t => t.id === typeId);
    state.objectTypeMappings[index].objectTypeId = typeId;
    state.objectTypeMappings[index].objectTypeName = type ? type.name : '';

    // Load attributes for this object type
    if (typeId) {
        try {
            const result = await fetch(`/api/jira/attributes/${typeId}?` + getJiraParams());
            const data = await result.json();
            state.objectTypeMappings[index].attributes = data.attributes || [];
        } catch (e) {
            console.error('Failed to load attributes:', e);
        }
    } else {
        state.objectTypeMappings[index].attributes = [];
    }

    renderObjectTypeMappings();
}

async function createNinjaDeviceIdAttribute(button) {
    const card = button.closest('.mapping-card');
    const index = parseInt(card.dataset.mappingIndex);
    const mapping = state.objectTypeMappings[index];

    if (!mapping.objectTypeId) {
        alert('Please select an object type first');
        return;
    }

    button.disabled = true;
    const originalText = button.textContent;
    button.textContent = 'Creating...';

    try {
        const params = getJiraParams();
        const result = await api('POST', `/api/jira/attributes/create?${params}`, {
            object_type_id: mapping.objectTypeId,
            name: 'NinjaOne Device ID',
            description: 'NinjaOne device ID for integration matching',
        });

        if (result.id) {
            // Add to attributes list
            if (!state.objectTypeMappings[index].attributes) {
                state.objectTypeMappings[index].attributes = [];
            }
            state.objectTypeMappings[index].attributes.push({
                id: result.id,
                name: result.name || 'NinjaOne Device ID',
            });

            // Also add as an attribute mapping with identity order 2 (after serial)
            state.objectTypeMappings[index].attributeMappings.push({
                jiraAttributeId: result.id,
                jiraAttributeName: result.name || 'NinjaOne Device ID',
                source: 'id',
                transform: '',
                identityOrder: 2,
            });

            alert('NinjaOne Device ID attribute created and added to mappings!');
            renderObjectTypeMappings();
        } else {
            let errorMsg = result.error || result.detail || result.message || 'Unknown error';
            if (typeof errorMsg === 'object') {
                errorMsg = JSON.stringify(errorMsg);
            }
            alert('Failed to create attribute: ' + errorMsg);
        }
    } catch (e) {
        alert('Failed to create attribute: ' + (e.message || String(e)));
    } finally {
        button.disabled = false;
        button.textContent = originalText;
    }
}

async function testMappingForRole(button) {
    const card = button.closest('.mapping-card');
    const index = parseInt(card.dataset.mappingIndex);
    const mapping = state.objectTypeMappings[index];

    if (!mapping.roleId) {
        alert('Please select a role first');
        return;
    }

    const resultDiv = card.querySelector('.mapping-test-result');
    resultDiv.innerHTML = '<p style="color: var(--text-muted);">Fetching sample device...</p>';

    try {
        const params = getNinjaParams();
        params.append('role_id', mapping.roleId);

        const result = await fetch('/api/ninjaone/sample-device?' + params);
        const data = await result.json();

        if (!data.device) {
            resultDiv.innerHTML = '<p style="color: var(--warning);">No devices found with this role</p>';
            return;
        }

        // Show mapping results
        let html = '<table><thead><tr><th>Jira Attribute</th><th>Value</th></tr></thead><tbody>';

        for (const m of (mapping.attributeMappings || [])) {
            if (m.jiraAttributeName && m.source) {
                const value = getNestedValue(data.device, m.source);
                html += `<tr>
                    <td>${m.jiraAttributeName}</td>
                    <td>${value !== undefined ? value : '<em style="color:var(--text-muted)">null</em>'}</td>
                </tr>`;
            }
        }

        html += '</tbody></table>';
        html += `<p style="margin-top: 12px; color: var(--success); font-size: 13px;">✓ Device: ${data.device.systemName || data.device.displayName}</p>`;
        resultDiv.innerHTML = html;

    } catch (e) {
        resultDiv.innerHTML = `<p style="color: var(--error);">Error: ${e.message}</p>`;
    }
}

function getNestedValue(obj, path) {
    if (!path) return undefined;
    const parts = path.replace(/\[(\d+)\]/g, '.$1').split('.');
    let current = obj;

    for (const part of parts) {
        if (current === null || current === undefined) return undefined;
        current = current[part];
    }

    return current;
}

// ─── Alerts Tab ──────────────────────────────────────────────────────────────

async function loadAlertsTabData() {
    if (state.alertsTabLoaded) return;

    const creds = getCredentials();
    if (!creds.jira_subdomain || !creds.jira_email || !creds.jira_api_token) return;

    await loadJiraProjects();
    renderAlertFieldMappings();

    state.alertsTabLoaded = true;
}

async function loadJiraProjects() {
    const select = document.getElementById('alert-project');
    select.innerHTML = '<option value="">Loading...</option>';

    try {
        const result = await fetch('/api/jira/projects?' + getJiraParams());
        const data = await result.json();
        state.jiraProjects = data.projects || [];

        select.innerHTML = '<option value="">-- Select Project --</option>';
        for (const project of state.jiraProjects) {
            const selected = state.issueConfig.projectKey === project.key ? 'selected' : '';
            select.innerHTML += `<option value="${project.key}" data-id="${project.id}" ${selected}>${project.name} (${project.key})</option>`;
        }

        // If a project was already selected, load its issue types
        if (state.issueConfig.projectKey) {
            await loadJiraIssueTypes(state.issueConfig.projectKey);
        }
    } catch (e) {
        console.error('Failed to load Jira projects:', e);
        select.innerHTML = '<option value="">-- Failed to load --</option>';
    }
}

async function onAlertProjectChange(select) {
    const key = select.value;
    const option = select.options[select.selectedIndex];
    state.issueConfig.projectKey = key;
    state.issueConfig.projectName = key ? option.text : '';
    state.issueConfig.issueTypeId = '';
    state.issueConfig.issueTypeName = '';

    const issueTypeSelect = document.getElementById('alert-issue-type');
    issueTypeSelect.innerHTML = '<option value="">-- Select project first --</option>';

    if (key) {
        await loadJiraIssueTypes(key);
    }
}

async function loadJiraIssueTypes(projectKey) {
    const select = document.getElementById('alert-issue-type');
    select.innerHTML = '<option value="">Loading...</option>';

    try {
        const params = getJiraParams();
        params.append('project_key', projectKey);
        const result = await fetch('/api/jira/issue-types?' + params);
        const data = await result.json();
        state.jiraIssueTypes = data.issue_types || [];

        select.innerHTML = '<option value="">-- Select Issue Type --</option>';
        for (const it of state.jiraIssueTypes) {
            const selected = state.issueConfig.issueTypeId === it.id ? 'selected' : '';
            select.innerHTML += `<option value="${it.id}" ${selected}>${it.name}</option>`;
        }

        // Auto-load fields and statuses if an issue type is already selected
        if (state.issueConfig.issueTypeId) {
            await loadJiraIssueFields();
            await loadProjectStatuses();
        }
    } catch (e) {
        console.error('Failed to load issue types:', e);
        select.innerHTML = '<option value="">-- Failed to load --</option>';
    }
}

async function onAlertIssueTypeChange(select) {
    state.issueConfig.issueTypeId = select.value;
    const option = select.options[select.selectedIndex];
    state.issueConfig.issueTypeName = select.value ? option.text : '';
    if (select.value) {
        await loadJiraIssueFields();
        await loadProjectStatuses();
    }
}

const NINJA_ALERT_FIELDS = [
    { value: 'severity',      label: 'severity' },
    { value: 'sourceType',    label: 'sourceType' },
    { value: 'conditionName', label: 'conditionName' },
    { value: 'message',       label: 'message' },
    { value: 'deviceId',      label: 'deviceId' },
];

function renderAlertFieldMappings() {
    const container = document.getElementById('alert-field-mappings-list');
    if (!container) return;
    container.innerHTML = '';
    if (state.issueConfig.alertFieldMappings.length === 0) {
        container.innerHTML = '<p style="color: var(--text-muted); font-size: 13px; margin: 0 0 8px;">No field mappings configured.</p>';
        return;
    }
    for (let i = 0; i < state.issueConfig.alertFieldMappings.length; i++) {
        const m = state.issueConfig.alertFieldMappings[i];
        const row = document.createElement('div');
        row.className = 'mapping-row';
        row.style.cssText = 'display:flex; gap:8px; align-items:center; margin-bottom:8px;';

        const ninjaSelect = document.createElement('select');
        ninjaSelect.style.flex = '1';
        for (const f of NINJA_ALERT_FIELDS) {
            const opt = document.createElement('option');
            opt.value = f.value;
            opt.textContent = f.label;
            if (f.value === m.ninjaField) opt.selected = true;
            ninjaSelect.appendChild(opt);
        }
        ninjaSelect.onchange = () => {
            state.issueConfig.alertFieldMappings[i].ninjaField = ninjaSelect.value;
        };

        const arrow = document.createElement('span');
        arrow.textContent = '→';
        arrow.style.color = 'var(--text-muted)';

        const jiraSelect = document.createElement('select');
        jiraSelect.style.flex = '1';
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = state.jiraIssueFields.length ? '— select field —' : '— select issue type first —';
        jiraSelect.appendChild(placeholder);
        for (const f of state.jiraIssueFields) {
            const opt = document.createElement('option');
            opt.value = f.id;
            opt.textContent = `${f.name} (${f.id})`;
            if (f.id === m.jiraFieldId) opt.selected = true;
            jiraSelect.appendChild(opt);
        }
        // If the saved value isn't in the list yet, add it so it doesn't disappear
        if (m.jiraFieldId && !state.jiraIssueFields.find(f => f.id === m.jiraFieldId)) {
            const opt = document.createElement('option');
            opt.value = m.jiraFieldId;
            opt.textContent = m.jiraFieldName ? `${m.jiraFieldName} (${m.jiraFieldId})` : m.jiraFieldId;
            opt.selected = true;
            jiraSelect.appendChild(opt);
        }
        jiraSelect.onchange = () => {
            const opt = jiraSelect.options[jiraSelect.selectedIndex];
            state.issueConfig.alertFieldMappings[i].jiraFieldId = jiraSelect.value;
            state.issueConfig.alertFieldMappings[i].jiraFieldName = opt ? opt.text : '';
        };

        const removeBtn = document.createElement('button');
        removeBtn.className = 'btn btn-small btn-danger';
        removeBtn.textContent = '×';
        removeBtn.onclick = () => removeAlertFieldMapping(i);

        row.appendChild(ninjaSelect);
        row.appendChild(arrow);
        row.appendChild(jiraSelect);
        row.appendChild(removeBtn);
        container.appendChild(row);
    }
}

function addAlertFieldMapping() {
    state.issueConfig.alertFieldMappings.push({ ninjaField: 'severity', jiraFieldId: '', jiraFieldName: '' });
    renderAlertFieldMappings();
}

function removeAlertFieldMapping(index) {
    state.issueConfig.alertFieldMappings.splice(index, 1);
    renderAlertFieldMappings();
}

async function loadJiraIssueFields() {
    if (!state.issueConfig.projectKey || !state.issueConfig.issueTypeId) return;
    try {
        const params = getJiraParams();
        params.append('project_key', state.issueConfig.projectKey);
        params.append('issue_type_id', state.issueConfig.issueTypeId);
        const data = await (await fetch('/api/jira/issue-fields?' + params)).json();
        state.jiraIssueFields = data.fields || [];
        renderAlertFieldMappings();
        renderAssetFieldDropdown();
        renderJsmFieldMappings();
    } catch (e) {
        console.error('Failed to load Jira issue fields:', e);
    }
}

function renderAssetFieldDropdown() {
    const sel = document.getElementById('asset-field-id');
    if (!sel) return;
    const current = state.issueConfig.assetFieldId;
    sel.innerHTML = '<option value="">— none / skip asset linking —</option>';
    const candidates = state.jiraIssueFields.filter(f =>
        f.id.startsWith('customfield_') && ['array', 'string', 'any', ''].includes(f.schema_type)
    );
    for (const f of candidates) {
        const selected = f.id === current ? 'selected' : '';
        sel.innerHTML += `<option value="${f.id}" ${selected}>${f.name} (${f.id})</option>`;
    }
    // Preserve a saved value not yet in the list
    if (current && !candidates.find(f => f.id === current)) {
        sel.innerHTML += `<option value="${current}" selected>${current}</option>`;
    }
}

// ─── JSM Value Mappings ──────────────────────────────────────────────────────

const NINJA_SEVERITY_VALUES = ['NONE', 'MINOR', 'MODERATE', 'MAJOR', 'CRITICAL'];
const NINJA_PRIORITY_VALUES = ['NONE', 'LOW', 'MEDIUM', 'HIGH'];

function ninjaValuesForSource(source) {
    return source === 'priority' ? NINJA_PRIORITY_VALUES : NINJA_SEVERITY_VALUES;
}

function renderJsmFieldMappings() {
    const container = document.getElementById('jsm-field-mappings-list');
    if (!container) return;
    container.innerHTML = '';

    if (state.issueConfig.jsmFieldMappings.length === 0) {
        container.innerHTML = '<p style="color: var(--text-muted); font-size: 13px; margin: 0 0 8px;">No JSM value mappings configured.</p>';
        return;
    }

    // Filter to option-type fields that have allowed values (or are known option types)
    const optionFields = state.jiraIssueFields.filter(f =>
        (f.allowed_values && f.allowed_values.length > 0) ||
        ['option', 'priority'].includes(f.schema_type)
    );

    state.issueConfig.jsmFieldMappings.forEach((m, i) => {
        const block = document.createElement('div');
        block.style.cssText = 'border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; padding: 12px; margin-bottom: 12px;';

        // Header row: Jira field + NinjaOne source + Remove button
        const headerRow = document.createElement('div');
        headerRow.style.cssText = 'display:flex; gap:8px; align-items:center; margin-bottom:10px;';

        const jiraFieldSel = document.createElement('select');
        jiraFieldSel.style.flex = '2';
        const jiraPlaceholder = document.createElement('option');
        jiraPlaceholder.value = '';
        jiraPlaceholder.textContent = optionFields.length ? '— select Jira field —' : '— select issue type first —';
        jiraFieldSel.appendChild(jiraPlaceholder);
        for (const f of optionFields) {
            const opt = document.createElement('option');
            opt.value = f.id;
            opt.textContent = `${f.name} (${f.id})`;
            if (f.id === m.jiraFieldId) opt.selected = true;
            jiraFieldSel.appendChild(opt);
        }
        if (m.jiraFieldId && !optionFields.find(f => f.id === m.jiraFieldId)) {
            const opt = document.createElement('option');
            opt.value = m.jiraFieldId;
            opt.textContent = m.jiraFieldName ? `${m.jiraFieldName} (${m.jiraFieldId})` : m.jiraFieldId;
            opt.selected = true;
            jiraFieldSel.appendChild(opt);
        }
        jiraFieldSel.onchange = () => {
            const opt = jiraFieldSel.options[jiraFieldSel.selectedIndex];
            state.issueConfig.jsmFieldMappings[i].jiraFieldId = jiraFieldSel.value;
            state.issueConfig.jsmFieldMappings[i].jiraFieldName = opt ? opt.text : '';
            renderJsmFieldMappings();
        };

        const ninjaSel = document.createElement('select');
        ninjaSel.style.flex = '1';
        for (const src of ['severity', 'priority']) {
            const opt = document.createElement('option');
            opt.value = src;
            opt.textContent = src;
            if (src === m.ninjaSource) opt.selected = true;
            ninjaSel.appendChild(opt);
        }
        ninjaSel.onchange = () => {
            const newSource = ninjaSel.value;
            const keys = ninjaValuesForSource(newSource);
            const newMap = {};
            for (const k of keys) newMap[k] = '';
            state.issueConfig.jsmFieldMappings[i].ninjaSource = newSource;
            state.issueConfig.jsmFieldMappings[i].valueMap = newMap;
            renderJsmFieldMappings();
        };

        const removeBtn = document.createElement('button');
        removeBtn.className = 'btn btn-small btn-danger';
        removeBtn.textContent = '×';
        removeBtn.onclick = () => removeJsmFieldMapping(i);

        headerRow.appendChild(jiraFieldSel);
        headerRow.appendChild(ninjaSel);
        headerRow.appendChild(removeBtn);
        block.appendChild(headerRow);

        // Value rows
        const fieldInfo = state.jiraIssueFields.find(f => f.id === m.jiraFieldId);
        const allowedValues = fieldInfo ? (fieldInfo.allowed_values || []) : [];
        const ninjaKeys = ninjaValuesForSource(m.ninjaSource);

        for (const ninjaVal of ninjaKeys) {
            const row = document.createElement('div');
            row.style.cssText = 'display:flex; gap:8px; align-items:center; margin-bottom:6px;';

            const label = document.createElement('span');
            label.style.cssText = 'flex:0 0 90px; font-size:12px; color:var(--text-muted); font-family:monospace;';
            label.textContent = ninjaVal;

            const arrow = document.createElement('span');
            arrow.textContent = '→';
            arrow.style.color = 'var(--text-muted)';

            let valueInput;
            if (allowedValues.length > 0) {
                valueInput = document.createElement('select');
                valueInput.style.flex = '1';
                const skipOpt = document.createElement('option');
                skipOpt.value = '';
                skipOpt.textContent = '— skip —';
                valueInput.appendChild(skipOpt);
                for (const av of allowedValues) {
                    const opt = document.createElement('option');
                    opt.value = av;
                    opt.textContent = av;
                    if (av === m.valueMap[ninjaVal]) opt.selected = true;
                    valueInput.appendChild(opt);
                }
            } else {
                valueInput = document.createElement('input');
                valueInput.type = 'text';
                valueInput.style.flex = '1';
                valueInput.placeholder = 'Jira value (e.g. Extensive / Widespread)';
                valueInput.value = m.valueMap[ninjaVal] || '';
            }
            valueInput.oninput = valueInput.onchange = () => {
                state.issueConfig.jsmFieldMappings[i].valueMap[ninjaVal] = valueInput.value;
            };

            row.appendChild(label);
            row.appendChild(arrow);
            row.appendChild(valueInput);
            block.appendChild(row);
        }

        container.appendChild(block);
    });
}

function addJsmFieldMapping() {
    state.issueConfig.jsmFieldMappings.push({
        jiraFieldId: '',
        jiraFieldName: '',
        ninjaSource: 'severity',
        valueMap: { NONE: '', MINOR: '', MODERATE: '', MAJOR: '', CRITICAL: '' },
    });
    renderJsmFieldMappings();
}

function removeJsmFieldMapping(index) {
    state.issueConfig.jsmFieldMappings.splice(index, 1);
    renderJsmFieldMappings();
}

async function loadProjectStatuses() {
    if (!state.issueConfig.projectKey || !state.issueConfig.issueTypeId) return;
    try {
        const params = getJiraParams();
        params.append('project_key', state.issueConfig.projectKey);
        params.append('issue_type_id', state.issueConfig.issueTypeId);
        const data = await (await fetch('/api/jira/project-statuses?' + params)).json();
        const statuses = data.statuses || [];
        populateStatusSelect('resolve-transition-select', statuses, state.issueConfig.resolveTargetStatus);
        populateStatusSelect('reopen-transition-select', statuses, state.issueConfig.reopenTargetStatus);
    } catch (e) {
        console.error('Failed to load project statuses:', e);
    }
}

function populateStatusSelect(selectId, statuses, currentValue) {
    const sel = document.getElementById(selectId);
    if (!sel) return;
    const noneLabel = selectId.includes('resolve') ? '— none / keep current status —' : '— select target status —';
    sel.innerHTML = `<option value="">${noneLabel}</option>`;
    for (const s of statuses) {
        const selected = s.name === currentValue ? 'selected' : '';
        sel.innerHTML += `<option value="${s.name}" ${selected}>${s.name}</option>`;
    }
    // Preserve a saved value not in the list
    if (currentValue && !statuses.find(s => s.name === currentValue)) {
        sel.innerHTML += `<option value="${currentValue}" selected>${currentValue}</option>`;
    }
}

// Tag input management (shared by sourceTypes)

function handleTagKeydown(event, listKey, inputId, containerId) {
    if (event.key !== 'Enter' && event.key !== ',') return;
    event.preventDefault();
    const input = document.getElementById(inputId);
    const value = input.value.trim().replace(/,$/, '');
    if (!value) return;
    if (!state.issueConfig[listKey].includes(value)) {
        state.issueConfig[listKey].push(value);
        renderTags(listKey, containerId, inputId);
    }
    input.value = '';
}

function removeTag(listKey, containerId, inputId, value) {
    state.issueConfig[listKey] = state.issueConfig[listKey].filter(v => v !== value);
    renderTags(listKey, containerId, inputId);
}

function renderTags(listKey, containerId, inputId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '';
    for (const value of state.issueConfig[listKey]) {
        const chip = document.createElement('span');
        chip.className = 'tag-chip';
        chip.innerHTML = `${value} <button type="button" onclick="removeTag('${listKey}', '${containerId}', '${inputId}', '${value}')">×</button>`;
        container.appendChild(chip);
    }
}

// ─── Retrigger toggle ────────────────────────────────────────────────────────

document.getElementById('retrigger-behavior')?.addEventListener('change', function() {
    const section = document.getElementById('reopen-config-section');
    if (section) section.style.display = this.value === 'reopen' ? '' : 'none';
});
// Initial state: hide reopen section
(function() {
    const section = document.getElementById('reopen-config-section');
    if (section) section.style.display = 'none';
})();

// ─── Config preview and save ──────────────────────────────────────────────────

function updateConfigPreview() {
    const config = buildConfig();

    // Summary
    const summary = document.getElementById('config-summary');
    const alertLine = state.issueConfig.projectKey
        ? `<p><strong>Alert Project:</strong> ${state.issueConfig.projectKey}</p>`
        : '';
    summary.innerHTML = `
        <p><strong>NinjaOne:</strong> ${getCredentials().ninja_base_url}</p>
        <p><strong>Jira:</strong> ${getCredentials().jira_subdomain}.atlassian.net</p>
        <p><strong>Role Mappings:</strong> ${state.objectTypeMappings.length}</p>
        ${alertLine}
    `;

    document.getElementById('config-preview').textContent = JSON.stringify(config, null, 2);
}

function buildConfig() {
    const creds = getCredentials();
    const schemaId = document.getElementById('jira-schema').value;
    const schema = state.schemas.find(s => s.id === schemaId);

    const config = {
        ninjaone: {
            base_url: creds.ninja_base_url,
            client_id: creds.ninja_client_id,
        },
        jira: {
            subdomain: creds.jira_subdomain,
            email: creds.jira_email,
            workspace_id: state.workspaceId || '',
        },
        assets: {
            schema_id: schemaId,
            schema_name: schema ? schema.name : '',
            object_type_mappings: state.objectTypeMappings
                .filter(m => m.roleId && m.objectTypeId)
                .map(m => ({
                    ninja_role_id: m.roleId,
                    ninja_role_name: m.roleName,
                    jira_object_type_id: m.objectTypeId,
                    jira_object_type_name: m.objectTypeName,
                    attribute_mappings: (m.attributeMappings || [])
                        .filter(a => a.jiraAttributeId && a.source)
                        .map(a => ({
                            jira_attribute_id: a.jiraAttributeId,
                            jira_attribute_name: a.jiraAttributeName,
                            jira_attribute_type: 'Default',
                            source: a.source,
                            required: false,
                            transform: a.transform || null,
                            identity_order: a.identityOrder || null,
                        })),
                })),
        },
        database: {
            path: 'data/integration.db',
        },
    };

    // Add issues config if a project is selected
    if (state.issueConfig.projectKey) {
        config.issues = {
            project_key: state.issueConfig.projectKey,
            issue_type_id: state.issueConfig.issueTypeId || '',
            issue_type_name: state.issueConfig.issueTypeName || '',
            summary_template: state.issueConfig.summaryTemplate || '',
            min_severity: state.issueConfig.minSeverity || null,
            source_types: state.issueConfig.sourceTypes,
            field_mappings: state.issueConfig.alertFieldMappings
                .filter(m => m.ninjaField && m.jiraFieldId)
                .map(m => ({
                    source: m.ninjaField,
                    jira_field_id: m.jiraFieldId,
                    jira_field_name: m.jiraFieldName || m.jiraFieldId,
                })),
            jsm_field_mappings: state.issueConfig.jsmFieldMappings
                .filter(m => m.jiraFieldId && m.ninjaSource)
                .map(m => ({
                    jira_field_id: m.jiraFieldId,
                    jira_field_name: m.jiraFieldName || '',
                    ninja_source: m.ninjaSource,
                    value_map: Object.fromEntries(
                        Object.entries(m.valueMap).filter(([, v]) => v)
                    ),
                })),
            asset_field_id: state.issueConfig.assetFieldId || '',
            resolve_target_status: state.issueConfig.resolveTargetStatus || null,
            resolve_comment: document.getElementById('resolve-comment')?.value?.trim() || '',
            retrigger_behavior: document.getElementById('retrigger-behavior')?.value || 'new_issue',
            reopen_target_status: state.issueConfig.reopenTargetStatus || null,
            reopen_comment: document.getElementById('reopen-comment')?.value?.trim() || '',
        };
    }

    // Add heartbeat/notifications config
    config.heartbeat = {
        enabled: state.notifConfig.enabled,
        url: state.notifConfig.url || null,
        token: state.notifConfig.token || null,
        interval_seconds: state.notifConfig.intervalSeconds,
        notify_on_changes: state.notifConfig.notifyOnChanges,
    };

    return config;
}

function exportConfig() {
    const config = buildConfig();
    const blob = new Blob([JSON.stringify(config, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'config.json';
    a.click();
    URL.revokeObjectURL(url);
}

async function saveConfig() {
    const config = buildConfig();
    const writeSecrets = document.getElementById('write-secrets').checked;

    if (writeSecrets) {
        const creds = getCredentials();
        config.ninjaone.client_secret = creds.ninja_client_secret;
        config.jira.api_token = creds.jira_api_token;
        const heartbeatToken = document.getElementById('heartbeat-token')?.value;
        if (heartbeatToken) {
            config.heartbeat = config.heartbeat || {};
            config.heartbeat.token = heartbeatToken;
        }
    }

    setSpinner('save-spinner', true);

    try {
        const result = await api('POST', '/api/config', {
            config,
            write_secrets: writeSecrets,
        });

        alert(`Configuration saved to ${result.path}\n\n` +
            (writeSecrets ? 'Secrets were written to the config file.' :
                'Remember to set environment variables:\nNINJA_CLIENT_SECRET=...\nJIRA_API_TOKEN=...'));

    } catch (e) {
        alert('Failed to save configuration: ' + e.message);
    } finally {
        setSpinner('save-spinner', false);
    }
}

// Load existing config on page load
async function loadExistingConfig() {
    try {
        // First load config (with redacted secrets)
        const config = await api('GET', '/api/config');

        if (config.ninjaone?.base_url) {
            document.getElementById('ninja-url').value = config.ninjaone.base_url;
        }
        if (config.ninjaone?.client_id) {
            document.getElementById('ninja-client-id').value = config.ninjaone.client_id;
        }
        if (config.jira?.subdomain) {
            document.getElementById('jira-subdomain').value = config.jira.subdomain;
        }
        if (config.jira?.email) {
            document.getElementById('jira-email').value = config.jira.email;
        }

        // Restore object type mappings if present
        if (config.assets?.object_type_mappings?.length > 0) {
            state.objectTypeMappings = config.assets.object_type_mappings.map(m => ({
                roleId: m.ninja_role_id,
                roleName: m.ninja_role_name,
                objectTypeId: m.jira_object_type_id,
                objectTypeName: m.jira_object_type_name,
                attributes: [],
                attributeMappings: (m.attribute_mappings || []).map(a => ({
                    jiraAttributeId: a.jira_attribute_id,
                    jiraAttributeName: a.jira_attribute_name,
                    source: a.source,
                    transform: a.transform || '',
                    identityOrder: a.identity_order || null,
                })),
            }));

            // Render the loaded mappings
            renderObjectTypeMappings();
        }

        // Restore issues config if present
        if (config.issues) {
            const ic = config.issues;
            state.issueConfig.projectKey = ic.project_key || '';
            state.issueConfig.projectName = ic.project_key || '';
            state.issueConfig.issueTypeId = ic.issue_type_id || '';
            state.issueConfig.issueTypeName = ic.issue_type_name || '';
            state.issueConfig.summaryTemplate = ic.summary_template || '[NinjaOne] {severity}: {device_name} - {message}';
            state.issueConfig.minSeverity = ic.min_severity || '';
            state.issueConfig.sourceTypes = ic.source_types || [];
            state.issueConfig.assetFieldId = ic.asset_field_id || '';
            state.issueConfig.resolveTargetStatus = ic.resolve_target_status || '';
            state.issueConfig.reopenTargetStatus = ic.reopen_target_status || '';
            state.issueConfig.alertFieldMappings = (ic.field_mappings || []).map(m => ({
                ninjaField: m.source || '',
                jiraFieldId: m.jira_field_id || '',
                jiraFieldName: m.jira_field_name || '',
            }));

            // Populate form fields
            const summaryInput = document.getElementById('alert-summary-template');
            if (summaryInput) summaryInput.value = state.issueConfig.summaryTemplate;
            const minSevSelect = document.getElementById('alert-min-severity');
            if (minSevSelect) minSevSelect.value = state.issueConfig.minSeverity;

            // Restore resolution & retrigger fields
            const resolveCommentInput = document.getElementById('resolve-comment');
            if (resolveCommentInput) resolveCommentInput.value = ic.resolve_comment || '';
            const rtSelect = document.getElementById('retrigger-behavior');
            if (rtSelect) rtSelect.value = ic.retrigger_behavior || 'new_issue';
            const reopenCommentInput = document.getElementById('reopen-comment');
            if (reopenCommentInput) reopenCommentInput.value = ic.reopen_comment || '';
            // Show/hide reopen section based on restored behavior
            const rtSection = document.getElementById('reopen-config-section');
            if (rtSection) rtSection.style.display = (ic.retrigger_behavior === 'reopen') ? '' : 'none';

            state.issueConfig.jsmFieldMappings = (ic.jsm_field_mappings || []).map(m => {
                const source = m.ninja_source || 'severity';
                const keys = source === 'priority'
                    ? ['NONE', 'LOW', 'MEDIUM', 'HIGH']
                    : ['NONE', 'MINOR', 'MODERATE', 'MAJOR', 'CRITICAL'];
                const valueMap = {};
                for (const k of keys) valueMap[k] = (m.value_map || {})[k] || '';
                return {
                    jiraFieldId: m.jira_field_id || '',
                    jiraFieldName: m.jira_field_name || '',
                    ninjaSource: source,
                    valueMap,
                };
            });

            renderTags('sourceTypes', 'source-types-tags', 'source-type-input');
            renderAlertFieldMappings();
            renderJsmFieldMappings();
        }

        // Restore notifications/heartbeat config
        if (config.heartbeat) {
            const hb = config.heartbeat;
            state.notifConfig.enabled = hb.enabled || false;
            state.notifConfig.url = hb.url || '';
            state.notifConfig.intervalSeconds = hb.interval_seconds || 60;
            state.notifConfig.notifyOnChanges = hb.notify_on_changes !== false;

            const enabledCb = document.getElementById('heartbeat-enabled');
            if (enabledCb) enabledCb.checked = state.notifConfig.enabled;
            const urlInput = document.getElementById('heartbeat-url');
            if (urlInput) urlInput.value = state.notifConfig.url;
            const intervalInput = document.getElementById('heartbeat-interval');
            if (intervalInput) intervalInput.value = state.notifConfig.intervalSeconds;
            const notifyChangesCb = document.getElementById('heartbeat-notify-changes');
            if (notifyChangesCb) notifyChangesCb.checked = state.notifConfig.notifyOnChanges;
            const notifSettings = document.getElementById('notif-settings');
            if (notifSettings) notifSettings.style.display = state.notifConfig.enabled ? '' : 'none';
        }

    } catch (e) {
        console.log('No existing config to load:', e);
    }

    // Then load secrets from environment/.env
    try {
        const secrets = await api('GET', '/api/config/secrets');

        // Pre-fill credentials from environment if available
        if (secrets.ninja_base_url) {
            // Find matching option in dropdown
            const ninjaUrl = document.getElementById('ninja-url');
            for (const opt of ninjaUrl.options) {
                if (secrets.ninja_base_url.includes(opt.value.replace('https://', ''))) {
                    ninjaUrl.value = opt.value;
                    break;
                }
            }
        }
        if (secrets.ninja_client_id && !document.getElementById('ninja-client-id').value) {
            document.getElementById('ninja-client-id').value = secrets.ninja_client_id;
        }
        if (secrets.ninja_client_secret) {
            document.getElementById('ninja-client-secret').value = secrets.ninja_client_secret;
        }
        if (secrets.jira_subdomain && !document.getElementById('jira-subdomain').value) {
            document.getElementById('jira-subdomain').value = secrets.jira_subdomain;
        }
        if (secrets.jira_email && !document.getElementById('jira-email').value) {
            document.getElementById('jira-email').value = secrets.jira_email;
        }
        if (secrets.jira_api_token) {
            document.getElementById('jira-token').value = secrets.jira_api_token;
        }

        console.log('Loaded secrets from environment');
    } catch (e) {
        console.log('Failed to load secrets:', e);
    }

    // Try to load roles and object types if credentials are available
    await loadDataIfCredentialsAvailable();
}

async function loadDataIfCredentialsAvailable() {
    // Try to load NinjaOne roles
    const ninjaUrl = document.getElementById('ninja-url').value;
    const ninjaClientId = document.getElementById('ninja-client-id').value;
    const ninjaClientSecret = document.getElementById('ninja-client-secret').value;

    if (ninjaUrl && ninjaClientId && ninjaClientSecret) {
        try {
            await loadRoles();
            console.log('Auto-loaded roles');
        } catch (e) {
            console.log('Could not auto-load roles:', e);
        }
    }

    // Try to load Jira schemas and object types
    const jiraSubdomain = document.getElementById('jira-subdomain').value;
    const jiraEmail = document.getElementById('jira-email').value;
    const jiraToken = document.getElementById('jira-token').value;

    if (jiraSubdomain && jiraEmail && jiraToken) {
        try {
            await loadSchemas();
            console.log('Auto-loaded schemas');

            // If we have a schema in config, select it and load types
            const schemaSelect = document.getElementById('jira-schema');
            for (const mapping of state.objectTypeMappings) {
                // Find schema that contains this object type
                // For now, just try all schemas until we find the types
                if (mapping.objectTypeId && state.schemas.length > 0) {
                    for (const schema of state.schemas) {
                        schemaSelect.value = schema.id;
                        await loadObjectTypes();

                        // Check if this schema has our object type
                        if (state.objectTypes.find(t => t.id === mapping.objectTypeId)) {
                            console.log(`Found object type ${mapping.objectTypeId} in schema ${schema.id}`);
                            break;
                        }
                    }
                    break; // Only need to find schema once
                }
            }

            // Load attributes for each mapping that has an object type
            for (let i = 0; i < state.objectTypeMappings.length; i++) {
                const mapping = state.objectTypeMappings[i];
                if (mapping.objectTypeId) {
                    try {
                        const params = getJiraParams();
                        const result = await api('GET', `/api/jira/attributes/${mapping.objectTypeId}?${params}`);
                        state.objectTypeMappings[i].attributes = result.attributes || [];
                        console.log(`Loaded ${state.objectTypeMappings[i].attributes.length} attributes for object type ${mapping.objectTypeId}`);
                    } catch (e) {
                        console.log(`Could not load attributes for object type ${mapping.objectTypeId}:`, e);
                    }
                }
            }

            // Re-render with loaded data
            renderObjectTypeMappings();

            // Pre-load alert project list so the Alerts tab is ready on arrival
            if (!state.alertsTabLoaded) {
                loadAlertsTabData();
            }

        } catch (e) {
            console.log('Could not auto-load Jira data:', e);
        }
    }
}

// Initialize
(async () => {
    await loadExistingConfig();
    renderObjectTypeMappings();
})();
