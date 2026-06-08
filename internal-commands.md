To generate fee details based on school and session
1) by session all school
`.venv/bin/python manage.py process_pending_fee_resyncs --batch-size 200 --max-sessions 20`
2) by school per session
`.venv/bin/python manage.py process_pending_fee_resyncs --session-id <ID> --batch-size 200
`
To generate mock data

1) Fresh dataset:
`./.venv/bin/python manage.py mock_data --size 60 --seed 42
`
2) Keep old mock rows and append:
`./.venv/bin/python manage.py mock_data --size 60 --seed 42 --keep-existing
`
python -c "import secrets; print(secrets.token_urlsafe(48))"

3) create db back up
`mysqldump -u root -p schoolstack > schoolstack_backup_$(date +%Y%m%d_%H%M%S).sql`