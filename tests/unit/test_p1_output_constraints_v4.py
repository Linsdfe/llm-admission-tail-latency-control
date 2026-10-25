"""Grammar answer isolation, native-client equivalence and real HTTP capture."""
import ast
import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT/relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

constraints = load('constraints_v4', 'scripts/replay/p1_output_constraints_v4.py')
client = load('structured_client_v4', 'scripts/replay/p1_structured_replay_v4.py')
KEYS = {'prompt','n_predict','temperature','stream','ignore_eos','cache_prompt','request_class','client_req_id'}


def prompt(command, source='资料\nR1|17\nR2|82'):
    return ('<|im_start|>system\n只输出结果，无表头。<|im_end|>\n'
        '<|im_start|>user\n'+source+'\n'+command+'<|im_end|>\n<|im_start|>assistant\n')


class ConstraintTests(unittest.TestCase):
    def test_numeric_domain_is_entire_registered_range_not_source_answer(self):
        text = prompt('按R2,R1顺序整理。每行ID|值。')
        self.assertEqual(constraints.parse_public_task(text,'interactive'),
            {'task_kind':'organize','target_ids':['R2','R1']})
        self.assertEqual(constraints.grammar_from_prompt(text,'interactive'),
            'root ::= "R2|" value "\\n" "R1|" value\nvalue ::= [1-9] [0-9]\n')
        grammar = constraints.grammar_from_prompt(prompt('提取R1。每行ID|值。'),'interactive')
        self.assertNotIn('17',grammar)
        # These source answers differ but must both remain legal alternatives.
        self.assertTrue(all(10 <= int(str(a)+str(b)) <= 99 for a in range(1,10) for b in range(10)))
        self.assertIn('[1-9] [0-9]',grammar)

    def test_check_allows_both_labels_and_background_fixes_six_ordered_ids(self):
        command = '核对R6,R2,R4,R3,R5,R1：同写一致，异写不一致。每行ID|结论。'
        grammar = constraints.grammar_from_prompt(prompt(command),'background')
        self.assertEqual(grammar.splitlines()[1],'value ::= "一致" | "不一致"')
        self.assertEqual(grammar.splitlines()[0],
            'root ::= "R6|" value "\\n" "R2|" value "\\n" "R4|" value "\\n" "R3|" value "\\n" "R5|" value "\\n" "R1|" value')

    def test_grammar_does_not_depend_on_source_values(self):
        command = '提取R1。每行ID|值。'
        first = constraints.constraint_from_prompt(prompt(command,'资料\nR1|17'),'interactive')
        second = constraints.constraint_from_prompt(prompt(command,'资料\nR1|99\nR2|34'),'interactive')
        self.assertEqual(first,second)
        self.assertEqual(first['constraint_source'],'public_final_task_instruction_only')
        self.assertEqual(len(first['grammar_sha256']),64)

    def test_unregistered_targets_instruction_and_envelope_are_rejected(self):
        commands = ['提取R1,R2。每行ID|值。','按R1,R1顺序整理。每行ID|值。',
            '按R2,R1顺序整理。每行ID|值。并解释。','提取R0。每行ID|值。',
            '提取R1"。每行ID|值。','核对R1：只写一致。每行ID|结论。']
        for command in commands:
            with self.subTest(command=command):
                with self.assertRaises(ValueError): constraints.grammar_from_prompt(prompt(command),'interactive')
        for text,category in [(prompt('提取R1。每行ID|值。'),'unknown'),
            (prompt('提取R1。每行ID|值。')+'extra','interactive'),
            ('提取R1。每行ID|值。','interactive')]:
            with self.assertRaises(ValueError): constraints.grammar_from_prompt(text,category)

    def test_api_has_no_answer_or_source_fact_parameter(self):
        with self.assertRaises(TypeError):
            constraints.grammar_from_prompt(prompt('提取R1。每行ID|值。'),'interactive',expected_answer='R1|17')

    def test_payload_has_only_eight_or_nine_fields_and_rejects_invalid_grammar(self):
        row = dict(req_id='v4-test',category='interactive',prompt='public prompt',max_tokens=64,
            cache_prompt=False,pair_id='test-pair',condition='unconstrained',
            expected_answer='SECRET_ORACLE',source_records=[{'value':'SECRET_ORACLE'}])
        self.assertEqual(set(client.completion_payload(row)),KEYS)
        constrained = dict(row,grammar='root ::= "R1|" value\nvalue ::= [1-9] [0-9]\n')
        self.assertEqual(set(client.completion_payload(constrained)),KEYS|{'grammar'})
        self.assertNotIn('SECRET_ORACLE',json.dumps(client.completion_payload(constrained)))
        for grammar in ('',' ',None,{},42):
            with self.assertRaises(ValueError): client.completion_payload(dict(row,grammar=grammar))

    def test_frozen_client_logic_unchanged_except_payload_and_docstring(self):
        old = ast.parse((ROOT/'scripts/replay/p1_development_replay.py').read_text(encoding='utf-8'))
        new = ast.parse((ROOT/'scripts/replay/p1_structured_replay_v4.py').read_text(encoding='utf-8'))
        old.body[0] = new.body[0]
        new.body = [node for node in new.body if not (isinstance(node,ast.FunctionDef) and node.name=='completion_payload')]
        replaced = 0
        for node in ast.walk(old):
            if (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
                and isinstance(node.func.value,ast.Name) and node.func.value.id=='json'
                and node.func.attr=='dumps' and node.args and isinstance(node.args[0],ast.Dict)
                and any(isinstance(key,ast.Constant) and key.value=='n_predict' for key in node.args[0].keys)):
                node.args[0] = ast.Call(func=ast.Name(id='completion_payload',ctx=ast.Load()),
                    args=[ast.Name(id='row',ctx=ast.Load())],keywords=[])
                replaced += 1
        self.assertEqual(replaced,1)
        self.assertEqual(ast.dump(old,include_attributes=False),ast.dump(new,include_attributes=False))


class WireCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_local_http_capture_two_modes_excludes_offline_metadata(self):
        captured = []
        async def handle(reader,writer):
            try:
                await reader.readline()
                headers = {}
                while (line := await reader.readline()) not in (b'\r\n',b'\n',b''):
                    key,value = line.decode().split(':',1)
                    headers[key.lower()] = value.strip()
                captured.append(json.loads(await reader.readexactly(int(headers['content-length']))))
                final = {'content':'R1|17','stop':True,'stop_type':'eos','truncated':False,
                    'tokens_evaluated':1,'tokens_predicted':1,
                    'timings':{'prompt_n':1,'cache_n':0,'predicted_n':1}}
                body = ('data: '+json.dumps(final)+'\n\n').encode()
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: '
                    +str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'+body)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(handle,'127.0.0.1',0)
        port = server.sockets[0].getsockname()[1]
        source = dict(req_id='v4-wire-u',category='interactive',arrival_offset_ms=0,
            prompt=prompt('提取R1。每行ID|值。'),input_tokens=1,max_tokens=64,
            cache_prompt=False,temperature=0,pair_id='wire',condition='unconstrained',
            expected_answer='SECRET_EVALUATION_ANSWER',expected_lines=['SECRET_EVALUATION_ANSWER'],
            source_records=[{'id':'R1','value':'SECRET_EVALUATION_ANSWER'}],
            target_ids=['SECRET_EVALUATION_ANSWER'],evaluation_only=True)
        constrained = dict(source,req_id='v4-wire-g',condition='constrained',
            grammar=constraints.grammar_from_prompt(source['prompt'],'interactive'))
        try:
            with tempfile.TemporaryDirectory() as folder:
                for index,row in enumerate((source,constrained)):
                    result = await client.replay([row],Path(folder)/str(index),f'http://127.0.0.1:{port}',.01,2,2,50,3)
                    self.assertEqual(result['terminal_counts']['completed'],1)
        finally:
            server.close()
            await server.wait_closed()
        self.assertEqual(len(captured),2)
        self.assertEqual(set(captured[0]),KEYS)
        self.assertEqual(set(captured[1]),KEYS|{'grammar'})
        self.assertEqual(captured[0],client.completion_payload(source))
        self.assertEqual(captured[1],client.completion_payload(constrained))
        self.assertNotIn('SECRET_EVALUATION_ANSWER',json.dumps(captured))


if __name__ == '__main__':
    unittest.main()
