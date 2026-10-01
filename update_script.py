import os
import re

# 1. Update requirements.txt
req_path = 'requirements.txt'
if os.path.exists(req_path):
    with open(req_path, 'r') as f:
        reqs = f.read()
    reqs = re.sub(r'bcrypt==4\.2\.0\n?', '', reqs)
    reqs = re.sub(r'a2wsgi>=1\.10\.10\n?', '', reqs)
    reqs = re.sub(r'python-multipart>=0\.0\.12\n?', '', reqs)
    reqs = re.sub(r'uvicorn>=0\.32\.0\n?', '', reqs)
    reqs = re.sub(r'fastapi>=0\.115\.0\n?', '', reqs)
    if 'flask' not in reqs.lower():
        reqs += '\nflask>=3.0.0\n'
    with open(req_path, 'w') as f:
        f.write(reqs.strip() + '\n')

# 2. Update settings.py
set_path = 'config/settings.py'
if os.path.exists(set_path):
    with open(set_path, 'r') as f:
        settings = f.read()
    settings = re.sub(r'API_HOST: str = os\.getenv\("API_HOST", "0\.0\.0\.0"\)\n?', '', settings)
    settings = re.sub(r'API_PORT: int = int\(os\.getenv\("API_PORT", "8000"\)\)\n?', '', settings)
    settings = re.sub(r'(?s)API_CORS_ORIGINS: list\[str\] = \[.*?\]\n?', '', settings)
    with open(set_path, 'w') as f:
        f.write(settings)

# 3. Add get_stale_pending to order_repo.py
repo_path = 'database/repositories/order_repo.py'
if os.path.exists(repo_path):
    with open(repo_path, 'r') as f:
        repo = f.read()
    if 'def get_stale_pending' not in repo:
        method = '''
    async def get_stale_pending(self, minutes: int = 10) -> list[Order]:
        \"\"\"Return orders still pending after minutes minutes.\"\"\"
        from datetime import timedelta
        cutoff = utcnow() - timedelta(minutes=minutes)
        result = await self.session.execute(
            select(Order)
            .where(Order.status == OrderStatus.pending, Order.created_at <= cutoff)
            .options(selectinload(Order.items), selectinload(Order.user))
        )
        return list(result.scalars().all())
'''
        repo += method
        with open(repo_path, 'w') as f:
            f.write(repo)
