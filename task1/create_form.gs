/**
 * Task 1A — Provision "Balto IT Request" Google Form + response Sheet.
 *
 * How to run:
 * 1. Open script.google.com → New project → paste this file.
 * 2. Run createBaltoITRequestForm() once (authorize as yourself).
 * 3. Copy the Form URL + Sheet URL from the execution log into your write-up.
 *
 * Design notes live in the submission PDF.
 */
function createBaltoITRequestForm() {
  const form = FormApp.create('Balto IT Request');
  form.setDescription(
    'Single front door for Balto IT. Pick a request type — you will only see questions for that type.\n' +
      'Urgent production access still goes here (set Urgency = Sev-1); do not DM the founder.'
  );
  form.setCollectEmail(true);
  form.setLimitOneResponsePerUser(false);
  form.setProgressBar(true);
  form.setConfirmationMessage('Got it — IT Ops will pick this up. Sev-1 requests page Slack #it-oncall.');

  // ----- Shared intake (every request) -----
  form.addSectionHeaderItem().setTitle('About you & this request');

  form
    .addTextItem()
    .setTitle('Manager email')
    .setHelpText('Okta manager profile email. Contractors: put your Balto sponsor.')
    .setRequired(true);

  form
    .addListItem()
    .setTitle('Urgency')
    .setChoiceValues([
      'Sev-1 — Production / customer-impacting now',
      'Sev-2 — Blocked on work today',
      'Sev-3 — Needed this week',
      'Sev-4 — Nice-to-have / planning',
    ])
    .setRequired(true);

  form
    .addParagraphTextItem()
    .setTitle('Business justification')
    .setHelpText('What outcome does this unlock? One or two sentences is enough.')
    .setRequired(true);

  form
    .addListItem()
    .setTitle('Data classification involved')
    .setHelpText('COMPLIANCE SIGNAL: highest classification you will touch with this request.')
    .setChoiceValues([
      'Public',
      'Internal',
      'Confidential (customer or employee data)',
      'Restricted (credentials, prod access, payment data)',
      'Not sure — please help me classify',
    ])
    .setRequired(true);

  const typeItem = form
    .addListItem()
    .setTitle('Request type')
    .setRequired(true);

  const softwareAccess = form.addPageBreakItem().setTitle('Software Access');
  _softwareAccessQuestions(form);
  const adminAccess = form.addPageBreakItem().setTitle('Admin / Elevated Access');
  _adminQuestions(form);
  const assistance = form.addPageBreakItem().setTitle('Software Assistance / Bug');
  _assistanceQuestions(form);
  const hardware = form.addPageBreakItem().setTitle('Hardware Request');
  _hardwareQuestions(form);
  const newTool = form.addPageBreakItem().setTitle('New Software Tool Evaluation');
  _newToolQuestions(form);
  const thanks = form.addPageBreakItem().setTitle('Submit');
  form.addSectionHeaderItem().setTitle('Ready to submit').setHelpText('Hit Submit. You will get a copy via email if enabled.');

  typeItem.setChoices([
    typeItem.createChoice('Software Access', softwareAccess),
    typeItem.createChoice('Admin / Elevated Access', adminAccess),
    typeItem.createChoice('Software Assistance / Bug', assistance),
    typeItem.createChoice('Hardware Request', hardware),
    typeItem.createChoice('New Software Tool Evaluation', newTool),
  ]);

  // After each branch, jump to thanks (avoid falling through into other branches)
  softwareAccess.setGoToPage(thanks);
  // PageBreak go-to is set on the break that ENDS a section — use navigation on list choices only.
  // Simpler approach: each section's last item is enough because users pick one type.
  // To prevent fall-through, set go-to on page breaks:
  const breaks = form.getItems(FormApp.ItemType.PAGE_BREAK);
  // softwareAccess → skip to thanks after its questions by setting next page breaks' destinations
  // FormApp limitation workaround: set GoToPage on each type's page break toward thanks for subsequent — 
  // Actually branching from ListItem already sends user to the right page. Fall-through happens when
  // they finish a middle section. Fix by pointing each section page break's GO TO:
  softwareAccess.setGoToPage(thanks); // This sets where we go WHEN LANDING? No —
  // Correct API: pageBreak.setGoToPage(page) controls where respondents go AFTER completing that page.
  // So the page break item titled Software Access is the START of that section; setGoToPage on it
  // controls navigation after that page's items... In Apps Script, setGoToPage on a PageBreakItem
  // sets the page to navigate to after the page break's page is completed.
  // We'll set each request-type page break to continue normally and add a final redirect item.
  // Practical fix used below: each branch ends by navigating to `thanks` via an explicit break.

  // Re-fetch and wire end-of-section navigation by inserting navigation breaks is complex;
  // Instead set go-to on the five section breaks to Thanks so completing that section submits path:
  softwareAccess.setGoToPage(thanks);
  adminAccess.setGoToPage(thanks);
  assistance.setGoToPage(thanks);
  hardware.setGoToPage(thanks);
  newTool.setGoToPage(thanks);

  const ss = SpreadsheetApp.create('Balto IT Request — Responses');
  form.setDestination(FormApp.DestinationType.SPREADSHEET, ss.getId());

  // Seed a Runs audit tab for Task 1C
  const runs = ss.insertSheet('AgentRuns');
  runs.appendRow([
    'timestamp',
    'response_row',
    'tool_name',
    'use_case',
    'destination',
    'destination_url',
    'model',
    'status',
    'notes',
  ]);

  Logger.log('FORM_URL=' + form.getPublishedUrl());
  Logger.log('FORM_EDIT_URL=' + form.getEditUrl());
  Logger.log('SHEET_URL=' + ss.getUrl());
  Logger.log('SHEET_ID=' + ss.getId());
  Logger.log('FORM_ID=' + form.getId());
}

function _softwareAccessQuestions(form) {
  form
    .addTextItem()
    .setTitle('Tool name (as it appears in Okta / our SSO catalog)')
    .setRequired(true);
  form
    .addTextItem()
    .setTitle('Exact role or permission set needed')
    .setHelpText('e.g. "Jira Software — Member", not "admin please"')
    .setRequired(true);
  form
    .addListItem()
    .setTitle('Is there an existing teammate with the same access we should mirror?')
    .setChoiceValues(['Yes — I will name them next', 'No — net-new access'])
    .setRequired(true);
  form.addTextItem().setTitle('Mirror access from (email)').setRequired(false);
  form
    .addListItem()
    .setTitle('Will this access include customer data?')
    .setHelpText('COMPLIANCE SIGNAL: drives manager approval + access review scope.')
    .setChoiceValues(['Yes', 'No', 'Unsure'])
    .setRequired(true);
  form
    .addDateItem()
    .setTitle('Access end date (required if temporary)')
    .setHelpText('Leave blank only for standing role access. Temporary elevated access MUST have an end date.');
}

function _adminQuestions(form) {
  form.addTextItem().setTitle('Tool where you need elevated access').setRequired(true);
  form
    .addParagraphTextItem()
    .setTitle('What elevated action do you need to perform?')
    .setHelpText('Be specific — "manage SCIM tokens" beats "need admin".')
    .setRequired(true);
  form
    .addListItem()
    .setTitle('Requested privilege tier')
    .setChoiceValues(['Write (non-admin)', 'App Admin', 'Org / Super Admin', 'Break-glass only'])
    .setRequired(true);
  form
    .addListItem()
    .setTitle('Duration')
    .setChoiceValues(['2 hours', '24 hours', '7 days', 'Standing (requires Director InfoSec)'])
    .setRequired(true);
  form
    .addCheckboxItem()
    .setTitle('Least-privilege checklist')
    .setHelpText('COMPLIANCE SIGNAL: SOC 2 change + access evidence.')
    .setChoiceValues([
      'I confirmed a lower role cannot do this',
      'I will not share the account / credentials',
      'I accept that standing admin requires InfoSec review',
    ])
    .setRequired(true);
  form
    .addParagraphTextItem()
    .setTitle('Rollback / revoke plan')
    .setHelpText('How do we know when to remove this?')
    .setRequired(true);
}

function _assistanceQuestions(form) {
  form.addTextItem().setTitle('Affected tool').setRequired(true);
  form
    .addListItem()
    .setTitle('Category')
    .setChoiceValues(['Bug / error', 'How-do-I', 'Performance', 'Login / SSO', 'Other'])
    .setRequired(true);
  form.addParagraphTextItem().setTitle('What were you trying to do?').setRequired(true);
  form.addParagraphTextItem().setTitle('What happened instead? (error text welcome)').setRequired(true);
  form
    .addListItem()
    .setTitle('How many people blocked?')
    .setHelpText('COMPLIANCE SIGNAL: Sev classification + potential incident evidence trail.')
    .setChoiceValues(['Just me', 'My team (2–10)', 'Company-wide / customers impacted'])
    .setRequired(true);
  form
    .addListItem()
    .setTitle('Does this involve a suspected security issue (phish, malware, unexpected admin email)?')
    .setChoiceValues(['No', 'Yes — treat as security incident', 'Unsure'])
    .setRequired(true);
}

function _hardwareQuestions(form) {
  form
    .addListItem()
    .setTitle('Hardware type')
    .setChoiceValues([
      'Replacement laptop',
      'New hire laptop (already ordered via People Ops? note below)',
      'Monitor',
      'Keyboard / mouse / headset',
      'Dock / adapter',
      'Other accessory',
    ])
    .setRequired(true);
  form.addParagraphTextItem().setTitle('Model preference + must-have specs').setRequired(true);
  form.addTextItem().setTitle('Ship-to country (ISO) + city').setRequired(true);
  form
    .addListItem()
    .setTitle('Is this replacing a lost/stolen device?')
    .setHelpText('COMPLIANCE SIGNAL: triggers wipe verification + incident ticket.')
    .setChoiceValues(['No', 'Yes — lost', 'Yes — stolen', 'Yes — failed/broken'])
    .setRequired(true);
  form
    .addTextItem()
    .setTitle('Estimated cost (USD) if known')
    .setHelpText('COMPLIANCE / FINANCE SIGNAL: purchases over $300 require manager approval; over $1,500 require CFO visibility.');
  form.addParagraphTextItem().setTitle('Current asset tag (if replacing Balto hardware)').setRequired(false);
}

function _newToolQuestions(form) {
  form.addTextItem().setTitle('Tool / vendor name').setRequired(true);
  form.addTextItem().setTitle('Vendor website URL').setRequired(true);
  form
    .addParagraphTextItem()
    .setTitle('Use case — what job should this tool do for Balto?')
    .setRequired(true);
  form
    .addListItem()
    .setTitle('Who will use it?')
    .setChoiceValues(['Just me', 'My team', 'Multiple departments', 'Company-wide'])
    .setRequired(true);
  form
    .addListItem()
    .setTitle('Will it process customer data or connect to production systems?')
    .setHelpText('COMPLIANCE SIGNAL: SOC 2 vendor review + DPA requirement trigger.')
    .setChoiceValues(['Yes', 'No', 'Unsure'])
    .setRequired(true);
  form
    .addListItem()
    .setTitle('Estimated annual spend')
    .setHelpText('FINANCE SIGNAL: >$5k/yr requires CFO path; any customer-data tool requires InfoSec.')
    .setChoiceValues(['Under $500', '$500–$5,000', '$5,000–$25,000', '$25,000+', 'Unknown'])
    .setRequired(true);
  form
    .addParagraphTextItem()
    .setTitle('What existing Balto tools did you evaluate instead (and why not)?')
    .setRequired(true);
  form
    .addListItem()
    .setTitle('Must-have: SSO (Okta) before purchase?')
    .setChoiceValues(['Yes — blocker without SSO', 'Preferred', 'Not required for this use case'])
    .setRequired(true);
}
