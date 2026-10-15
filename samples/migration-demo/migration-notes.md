# Account API v2 migration

## Creating users

POST /users now requires region. Valid regions are IN and US. Choose the region from the application's account configuration; these notes cannot determine the correct value for an individual account. The paused creation status is no longer accepted. Confirm the intended business state before choosing active.

## User identifiers

GET /users/{id} no longer returns legacyCode. Use the existing id field when constructing account links; no display-label replacement is documented.

## Legacy summary

GET /legacy was removed. No replacement operation is documented. Ask the API owner whether this summary is still required and how to obtain it.
