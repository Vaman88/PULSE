# Pulse

## Platform and stack
Web app. Existing FastAPI backend and plain HTML, CSS and JavaScript frontend.

## Users and purpose
Pharmacy staff track medicines and stock, and review demand plans informed by sales history and public health context.

## Confirmed inventory direction
The user selected Square's compact inventory table as the reference. Add an Inventory tab within Workspace and a persistent, account-scoped inventory screen. Support pharmacies starting fresh as well as future imports. Keep one explicit stock unit per medicine. Future barcode package mappings should convert to that unit rather than mix units in its history.

## Scope of this iteration
Inventory catalog and current balances, add/edit/archive/restore, search, stock and archive filters, name or update-date sorting, 25-row pagination and CSV export of all matching rows. Each medicine's stock unit is fixed after creation; staff can record a barcode or product code manually. USB/Bluetooth keyboard scanners capture product barcodes and match saved medicines; unknown codes can be linked or used to start a new entry. External product lookup, camera scanning, automatic CSV interpretation and automatic forecast updates are later features. Existing forecasts and signals retain their current behavior.
