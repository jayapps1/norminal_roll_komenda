# Codex Agent Guidance

## Project shape

- This is a Django 5.2.6 personnel-roll application configured by `base.settings`.
- `staff/` owns staff-user registration, login, and logout using Django auth.
- `gnfs_komenda/` owns the `NorminalRoll` model, personnel CRUD, leave-status views, Excel import/export, templates, and the `reset_expired_leaves` management command.
- Shared layout templates are in `templates/`; app templates are under each app's `templates/` directory. Project static assets are under `static/gnfs_komenda/`.
- The default database is the repository-root `db.sqlite3`. Treat existing records as meaningful during development.

## Environment and commands

- Use the repository virtual environment on Windows: `env\\Scripts\\activate` or `env\\Scripts\\python.exe`.
- Dependencies are pinned in `requirements.txt`; do not install packages globally.
- Run the Django system check after configuration or code changes:
  `python manage.py check`
- Run the test suite with:
  `python manage.py test`
- Apply migrations with:
  `python manage.py migrate`
- Run the development server with:
  `python manage.py runserver`
- Run the leave cleanup command explicitly with:
  `python manage.py reset_expired_leaves`
- Seed or repair the application login account with `seed_admin`; by default
  this account is not granted Django admin access unless `--superuser` or `--is-staff` is specified.
  Prefer the `APP_ADMIN_PASSWORD` environment variable or the interactive prompt:
  `python manage.py seed_admin --username admin`
- Pass `--superuser` to grant Django admin and staff access:
  `python manage.py seed_admin --username nanagyachie@live.com --superuser`
- Use `--reset-password` only when the existing application password should change.
- No project-specific formatter, linter, or frontend build tool is configured.

## Implementation conventions

- Follow existing Django patterns: function views for simple leave/auth pages, class-based generic views for personnel CRUD, `ModelForm` validation in `gnfs_komenda/forms.py`, and named URL routes for redirects and template links.
- Preserve URL names and the `staffID` lookup contract unless the change explicitly requires a migration and coordinated template/view updates.
- Keep business rules for `called_off`, `manual_override`, leave dates, and `leave_status` in the model or form validation rather than duplicating them in templates or JavaScript.
- Normalize personnel text fields consistently with the existing uppercase behavior in create/edit views and forms.
- Use Django ORM queries and timezone-aware utilities. Do not add direct SQL for ordinary application behavior.
- Include CSRF tokens in POST forms and retain authentication protection for personnel operations and file import/export.
- For Excel changes, preserve the expected `NorminalRoll` field names and handle invalid rows without silently losing import results.
- Add or update migrations whenever model fields or choices change; do not edit existing migration history to rewrite applied schema.
- Keep static JavaScript defensive: shared scripts are loaded by `templates/main.html`, so code must tolerate pages where an optional element is absent.
- Keep changes focused. Avoid modifying vendored files under `env/` or `venv/` and avoid committing generated static output unless explicitly required.

## Change workflow

1. Inspect the owning model/view/form/template and a nearby test before editing.
2. Make the smallest change that preserves existing route names, template inheritance, and database behavior.
3. Run `python manage.py check`, then the narrowest relevant test or `python manage.py test`.
4. For schema changes, run migrations in the local environment and verify the resulting migration is present.
5. Review the diff for accidental changes to `db.sqlite3`, virtual environments, credentials, or unrelated assets.

## Known project risks

- `base/settings.py` currently enables `DEBUG` and contains a development secret key; never treat these settings as production-safe or expose the key.
- Personnel data includes identity, payroll, bank, and leave information. Avoid logging sensitive field values, especially during imports and error handling.
- Authentication and authorization are not uniformly applied to every personnel view; check access control when adding or exposing a route.
- Existing JavaScript assumes some element IDs exist on pages. Guard optional selectors before attaching listeners.
- Leave behavior is date-sensitive and uses UTC (`TIME_ZONE = 'UTC'`, `USE_TZ = True`); test boundary dates explicitly.
