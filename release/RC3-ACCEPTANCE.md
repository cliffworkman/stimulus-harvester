# 0.2.0rc3 acceptance fix: unconfigured Screen form

The unchanged rc2 runtime reproduced the reported three-field ValidationError.
The exact trigger is Screen: leave the detector unselected and both thresholds
blank, select a rule, press Save explicit policy and confirm. The user's recorded
rc2 screen-policy request confirms this path (with device CUDA). Another recorded
attempt selected MobileNet but left the thresholds blank.

ScreenPage.save_policy submitted those controls without validation. The workspace
recipe constructed a temporary detector dictionary and HarvesterConfig correctly
rejected it before the atomic configuration write. Invalid request fields survive
in the failed-operation audit record; the active project configuration remains valid
with no detectors. No repair is needed for the inspected project or this failed-save
path, and historical audit records must not be edited or deleted.

The fix validates the Screen form before confirmation or submission. No selected
model means no save and no detector entry. It also preserves any previously saved
policy; removing an existing policy still requires Disable configured screening.
A selected model requires explicit, nonblank thresholds validated against the existing
DetectorConfig schema, plus an explicit rule. Valid fields use the existing audited
save operation. No model or threshold is filled automatically; Pydantic and backend
validation are unchanged.

Project creation/opening, advanced JSON saving, generic configuration saving and
Discover/provider saving do not serialize the pending Screen form. Discover updates
only search fields in a copy of the saved configuration. Provider setup dialogs only
update their own controls. Regression cases cover these boundaries, both providers'
success/cancel paths, cleared selection, valid models, invalid thresholds, preservation
of existing screening and strict rejection of manually supplied invalid JSON.

This material acceptance fix advances the package to 0.2.0rc3 / Windows 0.2.0.3.
Rc2 artifacts and their hashes are preserved. The existing intentional unsigned
distribution policy is carried into the new build, including its previously approved
documentation and deferred signing instructions. No release tag or publication is
authorized. Fresh Windows, remaining human acceptance and native redistribution
clearance remain open. Rc2 test results must not be relabeled as rc3 validation.
