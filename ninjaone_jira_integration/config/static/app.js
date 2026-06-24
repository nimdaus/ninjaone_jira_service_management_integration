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

    // Set NinjaOne field selection (now first column)
    const ninjaSelect = row.querySelector('.ninja-field-select');
    ninjaSelect.value = attrMapping.source || '';
    ninjaSelect.onchange = () => updateAttributeMapping(mappingIndex, attrIndex, 'source', ninjaSelect.value);

    // Populate Jira attribute dropdown (now second column)
    const jiraSelect = row.querySelector('.jira-attr-select');

    if (availableAttrs.length === 0) {
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

    // Populate transforms list
    const transformsList = row.querySelector('.transforms-list');
    const transforms = attrMapping.transforms || (attrMapping.transform ? [attrMapping.transform] : []);
    for (const t of transforms) {
        const item = createTransformItemElement(t);
        transformsList.appendChild(item);
    }

    // Set identity order
    const identityInput = row.querySelector('.identity-order-input');
    identityInput.value = attrMapping.identityOrder || '';
    identityInput.onchange = () => {
        const val = identityInput.value ? parseInt(identityInput.value) : null;
        updateAttributeMapping(mappingIndex, attrIndex, 'identityOrder', val);
    };

    return row;
}

function createTransformItemElement(value) {
    const template = document.getElementById('transform-item-template');
    const clone = template.content.cloneNode(true);
    const item = clone.querySelector('.transform-item');
    const select = item.querySelector('.transform-item-select');
    if (value) {
        select.value = value;
    }
    return item;
}

function addTransformItem(button) {
    const transformsList = button.closest('.transforms-container').querySelector('.transforms-list');
    const item = createTransformItemElement('');
    transformsList.appendChild(item);
}

function removeTransformItem(button) {
    button.closest('.transform-item').remove();
}

function getTransformsFromRow(row) {
    return Array.from(row.querySelectorAll('.transform-item-select'))
        .map(s => s.value)
        .filter(v => v !== '');
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
        transforms: [],
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
                transforms: [],
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

// Config preview and save
function updateConfigPreview() {
    const config = buildConfig();

    // Summary
    const summary = document.getElementById('config-summary');
    summary.innerHTML = `
        <p><strong>NinjaOne:</strong> ${getCredentials().ninja_base_url}</p>
        <p><strong>Jira:</strong> ${getCredentials().jira_subdomain}.atlassian.net</p>
        <p><strong>Role Mappings:</strong> ${state.objectTypeMappings.length}</p>
    `;

    document.getElementById('config-preview').textContent = JSON.stringify(config, null, 2);
}

function buildConfig() {
    const creds = getCredentials();
    const schemaId = document.getElementById('jira-schema').value;
    const schema = state.schemas.find(s => s.id === schemaId);

    return {
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
                .map((m, mIdx) => ({
                    ninja_role_id: m.roleId,
                    ninja_role_name: m.roleName,
                    jira_object_type_id: m.objectTypeId,
                    jira_object_type_name: m.objectTypeName,
                    attribute_mappings: (m.attributeMappings || [])
                        .filter(a => a.jiraAttributeId && a.source)
                        .map((a, aIdx) => {
                            // Read transforms from rendered DOM (user may have added/removed items)
                            const row = document.querySelector(
                                `.mapping-row[data-mapping-index="${mIdx}"][data-attr-index="${aIdx}"]`
                            );
                            const transforms = row ? getTransformsFromRow(row) : (a.transforms || []);
                            return {
                                jira_attribute_id: a.jiraAttributeId,
                                jira_attribute_name: a.jiraAttributeName,
                                jira_attribute_type: 'Default',
                                source: a.source,
                                required: false,
                                transforms: transforms,
                                identity_order: a.identityOrder || null,
                            };
                        }),
                })),
        },
        database: {
            path: 'data/integration.db',
        },
    };
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
                    transforms: a.transforms || (a.transform ? [a.transform] : []),
                    identityOrder: a.identity_order || null,
                })),
            }));

            // Render the loaded mappings
            renderObjectTypeMappings();
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
