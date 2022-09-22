# Scheduled Jobs
Below is a description of the various automated jobs that MetaDeploy has and how they can be configured.


## `cleanup_user_data`

Frequency: every minute

This job does four key things:

1. Sets the status of any jobs which were started but are past their timeout to "canceled" and updates the `canceled_at` and `exception` fields on the corresponding `Job` record.
2. Delete any OAuth tokens older than 10 minutes if the user doesn't have any running jobs currently. This can be configured with the TOKEN_LIFETIME_MINUTES environment variable.
3. Deletes all non-staff users that have not logged in for the last thirty days.
4. Clears the exception field in `Job` and `Preflight` records over 90 days old. (This field may contain customer metadata such as custom schema names from the org).

## `expire_preflight_results`

Frequency: every minute

Invalidates any preflight checks that were created more than 10 minutes ago. This can be configured to a custom value by setting the PREFLIGHT_LIFETIME_MINUTES environment variable.

## `calculate_average_plan_runtimes`

Frequency: daily

Calculates the average plan runtime for all plans, and then stores the value. Pages that need to reference plan runtime reference the calculated value for quicker page loads.