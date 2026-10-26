"""Disposable Docker/psql harness. Never reads .env, DSNs, or app configuration."""
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
IMAGE = 'postgres@sha256:b8faeff7712663c2773a190181e1246e37e364b887611c04658c28b44e2408fe'


def literal(value):
    if value is None: return 'NULL'
    if isinstance(value, (dict,list)): value=json.dumps(value,ensure_ascii=False)
    return "'"+str(value).replace("'","''")+"'"


class LocalDB:
    def __init__(self):
        if os.environ.get('BLIPZ_V2_ISOLATED_DB_TESTS')!='1':
            raise RuntimeError('Explicit BLIPZ_V2_ISOLATED_DB_TESTS=1 is required')
        self.name='blipz-v2-isolated-'+uuid4().hex[:12]

    def start(self):
        subprocess.run(['docker','run','--detach','--rm','--name',self.name,'--label','blipz.disposable-v2-test=true','--network','none','--tmpfs','/var/lib/postgresql/data','-e','POSTGRES_HOST_AUTH_METHOD=trust','-e','POSTGRES_DB=blipz_v2_isolated',IMAGE],check=True,capture_output=True,text=True)
        try:
            for _ in range(60):
                if subprocess.run(['docker','exec',self.name,'pg_isready','-h','127.0.0.1','-U','postgres','-d','blipz_v2_isolated'],capture_output=True).returncode==0:break
                time.sleep(0.2)
            else:raise RuntimeError('Isolated PostgreSQL did not become ready')
            info=json.loads(subprocess.check_output(['docker','inspect',self.name],text=True))[0]
            assert info['HostConfig']['NetworkMode']=='none' and not info['HostConfig']['PortBindings']
            self.bootstrap()
        except BaseException:
            self.stop();raise
        return self

    def stop(self):
        subprocess.run(['docker','rm','--force','--volumes',self.name],check=True,capture_output=True)

    def sql(self,sql,*,ok=True):
        result=subprocess.run(['docker','exec','-i',self.name,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','postgres','-d','blipz_v2_isolated'],input=sql,text=True,capture_output=True,timeout=30)
        if ok and result.returncode:raise AssertionError(result.stderr)
        if not ok and not result.returncode:raise AssertionError('Expected SQL failure')
        return result.stdout.strip() if ok else result.stderr

    def value(self,expression):return json.loads(self.sql('SELECT to_jsonb(('+expression+'));'))

    def bootstrap(self):
        self.sql('CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN; CREATE ROLE service_role NOLOGIN BYPASSRLS; CREATE SCHEMA auth; CREATE TABLE auth.users(id uuid PRIMARY KEY,email text);')
        base=(ROOT/'sql/migrations.sql').read_text()
        # Historical replay repairs already-declared FKs a second time. Omit ONLY
        # that obsolete repair block in this disposable bootstrap; never edit V1 SQL.
        begin=base.index('-- Fix schema drift:')
        end=base.index('-- Explicit per-game completion state')
        base=base[:begin]+base[end:]
        self.sql(base)
        for name in ['20260814_daily_content_hardening.sql','20261002_atomic_daily_content_replacement.sql','20261003_atomic_game_completion.sql']:
            self.sql((ROOT/'sql'/name).read_text())
        self.sql("INSERT INTO public.users(id,username,current_streak,longest_streak) VALUES ('11111111-1111-4111-8111-111111111111','local_v1_baseline',2,5); INSERT INTO public.scores(user_id,date,guess_score,guess_completed,total_score) VALUES ('11111111-1111-4111-8111-111111111111','2026-08-14',8,true,32);")
        self.baseline=self.v1_snapshot()

    def v1_snapshot(self):
        schema=self.sql("SELECT jsonb_agg(to_jsonb(x) ORDER BY x.table_name,x.ordinal_position) FROM information_schema.columns x WHERE table_schema='public';")
        constraints=self.sql("SELECT coalesce(jsonb_agg(jsonb_build_array(c.relname,k.conname,pg_get_constraintdef(k.oid)) ORDER BY c.relname,k.conname),'[]') FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public';")
        functions=self.sql("SELECT jsonb_agg(pg_get_functiondef(p.oid) ORDER BY p.proname) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public';")
        rows=self.sql("SELECT jsonb_build_array((SELECT jsonb_agg(to_jsonb(u)) FROM public.users u WHERE username='local_v1_baseline'),(SELECT jsonb_agg(to_jsonb(s)) FROM public.scores s WHERE user_id='11111111-1111-4111-8111-111111111111'));")
        return schema,constraints,functions,rows

    def migrate(self,name):self.sql((ROOT/'sql'/name).read_text())

    def seed(self,day_offset=0):
        assert type(day_offset) is int and abs(day_offset)<=1
        c=json.loads((ROOT/'docs/v2-contracts/fixtures/content.json').read_text())
        # Only this harness changes synthetic timestamps/approval. These are local DB
        # fixtures, never claims about reviewed or production-approved content.
        self.sql('TRUNCATE blipz_v2.daily_activity,blipz_v2.daily_game_attempts,blipz_v2.daily_results,blipz_v2.daily_game_instances,blipz_v2.daily_challenges,blipz_v2_private.game_solutions,blipz_v2.game_content_revisions,blipz_v2.fallback_packages CASCADE;')
        self.sql("DELETE FROM public.scores WHERE user_id IN (SELECT id FROM public.users WHERE username LIKE 'local_db_%');")
        user=str(uuid4()); challenge=c['challenge']['id']
        statements=['BEGIN;',f"INSERT INTO public.users(id,username,current_streak,longest_streak) VALUES ({literal(user)},{literal('local_db_'+user)},0,0);"]
        for r in c['revisions'][:4]:
            private=r['private_solution'];aliases=private.get('answers',{}).get('accepted_aliases',[])
            # Reuse the approved pure Python normalizer, without importing app.config.
            from app.v2.answers import normalize_answer
            normalized=[normalize_answer(a) for a in aliases]
            statements.append(f"INSERT INTO blipz_v2.game_content_revisions(id,game_type,variant,schema_version,generator_version,status,public_payload,content_sha256,validation,validated_at,approved_by,approved_at) VALUES ({literal(r['id'])},{literal(r['game_type'])},{literal(r['variant'])},1,'isolated-test-only','approved',{literal(r['public_payload'])}::jsonb,{literal(r['validation']['content_sha256'])},'{{\"human_approval\":\"approved\"}}',clock_timestamp(),'isolated-fixture',clock_timestamp());")
            statements.append(f"INSERT INTO blipz_v2_private.game_solutions(content_revision_id,solution_payload,normalized_aliases) VALUES ({literal(r['id'])},{literal(private)}::jsonb,ARRAY[{','.join(literal(a) for a in normalized)}]::text[]);")
        statements.append(f"INSERT INTO blipz_v2.daily_challenges(id,date,lineup_revision,status,opens_at,closes_at) VALUES ({literal(challenge)},(clock_timestamp() AT TIME ZONE 'UTC')::date+{day_offset},{literal(c['challenge']['lineup_revision'])},'draft',date_trunc('day',(clock_timestamp() AT TIME ZONE 'UTC')+({day_offset}*interval '24 hours')) AT TIME ZONE 'UTC',(date_trunc('day',(clock_timestamp() AT TIME ZONE 'UTC')+({day_offset}*interval '24 hours')) AT TIME ZONE 'UTC')+interval '24 hours');")
        for i in c['challenge']['games']:
            statements.append(f"INSERT INTO blipz_v2.daily_game_instances(id,challenge_id,position,game_type,variant,content_revision_id,title,presentation_role,max_points_units,answer_policy) VALUES ({literal(i['id'])},{literal(challenge)},{i['position']},{literal(i['game_type'])},{literal(i['variant'])},{literal(i['content_revision_id'])},{literal(i['title'])},{literal(i['presentation_role'])},2500,{literal(i['answer_policy'])}::jsonb);")
        statements.append(f"UPDATE blipz_v2.daily_challenges SET status='published',published_at=clock_timestamp() WHERE id={literal(challenge)}; COMMIT;")
        self.sql('\n'.join(statements))
        return user,c['challenge']['games']
