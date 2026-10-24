from pathlib import Path
import difflib

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
UPSTREAM = ROOT / 'third_party/llama.cpp'
FILES = ['tools/server/server-task.h', 'tools/server/server-queue.h',
         'tools/server/server-queue.cpp', 'tools/server/server-context.cpp']


def replace_once(text, old, new):
    assert text.count(old) == 1, (old[:100], text.count(old))
    return text.replace(old, new, 1)


sources = {name: (UPSTREAM / name).read_text(encoding='utf-8') for name in FILES}
modified = dict(sources)

p = 'tools/server/server-task.h'
modified[p] = replace_once(modified[p], '    int id_slot   = -1;\n', '''    int id_slot   = -1;

    bool schedule_background = false;
    int64_t schedule_enqueued_ms = 0;
    std::string schedule_client_req_id;
''')
modified[p] = replace_once(modified[p], '        copy.id_slot   = -1; // child tasks cannot specify slot\n', '''        copy.id_slot   = -1; // child tasks cannot specify slot
        copy.schedule_background = schedule_background;
        copy.schedule_enqueued_ms = schedule_enqueued_ms;
        copy.schedule_client_req_id = schedule_client_req_id;
''')

p = 'tools/server/server-queue.h'
modified[p] = replace_once(modified[p], '    int64_t time_last_task = 0;\n', '''    int64_t time_last_task = 0;
    bool simple_scheduler_enabled = false;
    int64_t background_aging_ms = 5000;
''')
modified[p] = replace_once(modified[p], '    ~server_queue() { worker_stop(); }\n', '''    ~server_queue() { worker_stop(); }

    void configure_simple_scheduler(bool enabled, int64_t aging_ms = 5000) {
        GGML_ASSERT(!worker.thread.joinable());
        GGML_ASSERT(aging_ms > 0);
        simple_scheduler_enabled = enabled;
        background_aging_ms = aging_ms;
    }

    bool simple_scheduler_active() const { return simple_scheduler_enabled; }
''')
modified[p] = replace_once(modified[p], '    void cleanup_pending_task(int id_target);\n', '''    void cleanup_pending_task(int id_target);
    void stamp_scheduled_task(server_task & task, int64_t now_ms);
    int scheduling_priority(const server_task & task, int64_t now_ms) const;
    std::deque<server_task>::iterator select_scheduled_task(std::deque<server_task> & tasks, int64_t now_ms, int id_slot = -1);
    void log_scheduled_task(const server_task & task, int64_t now_ms, const char * source, const char * original_reason) const;
''')

p = 'tools/server/server-queue.cpp'
modified[p] = replace_once(modified[p], 'int server_queue::post(server_task && task, bool front) {\n', '''void server_queue::stamp_scheduled_task(server_task & task, int64_t now_ms) {
    if (!task.need_sampling()) {
        return;
    }
    if (task.schedule_enqueued_ms == 0) {
        task.schedule_enqueued_ms = now_ms;
    }
    for (auto & child : task.child_tasks) {
        stamp_scheduled_task(child, task.schedule_enqueued_ms);
    }
    QUE_INF("SCHED_ENQUEUE id=%d clientid=%s class=%s enqueue_ms=%lld mode=%d pending=%zu deferred=%zu\\n",
            task.id, task.schedule_client_req_id.empty() ? "-" : task.schedule_client_req_id.c_str(),
            task.schedule_background ? "background" : "interactive", (long long) task.schedule_enqueued_ms,
            simple_scheduler_enabled, queue_tasks.size(), queue_tasks_deferred.size());
}

int server_queue::scheduling_priority(const server_task & task, int64_t now_ms) const {
    if (!task.need_sampling()) {
        return -1;
    }
    if (task.schedule_background) {
        return now_ms - task.schedule_enqueued_ms >= background_aging_ms ? 0 : 2;
    }
    return 1;
}

std::deque<server_task>::iterator server_queue::select_scheduled_task(std::deque<server_task> & tasks, int64_t now_ms, int id_slot) {
    auto selected = tasks.end();
    for (auto it = tasks.begin(); it != tasks.end(); ++it) {
        if (id_slot != -1 && it->id_slot != -1 && it->id_slot != id_slot) {
            continue;
        }
        if (selected == tasks.end()) {
            selected = it;
            continue;
        }
        const int priority = scheduling_priority(*it, now_ms);
        const int priority_selected = scheduling_priority(*selected, now_ms);
        if (priority < priority_selected ||
                (priority == priority_selected && priority >= 0 &&
                 (it->schedule_enqueued_ms < selected->schedule_enqueued_ms ||
                  (it->schedule_enqueued_ms == selected->schedule_enqueued_ms && it->id < selected->id)))) {
            selected = it;
        }
    }
    return selected;
}

void server_queue::log_scheduled_task(const server_task & task, int64_t now_ms, const char * source, const char * original_reason) const {
    if (!task.need_sampling()) {
        return;
    }
    const char * reason = original_reason;
    if (simple_scheduler_enabled) {
        reason = task.schedule_background
            ? (scheduling_priority(task, now_ms) == 0 ? "background_aged" : "background_fifo")
            : "interactive";
    }
    QUE_INF("SCHED_SELECT id=%d clientid=%s class=%s reason=%s wait_ms=%lld pending=%zu deferred=%zu source=%s\\n",
            task.id, task.schedule_client_req_id.empty() ? "-" : task.schedule_client_req_id.c_str(),
            task.schedule_background ? "background" : "interactive", reason,
            (long long) (now_ms - task.schedule_enqueued_ms), queue_tasks.size(), queue_tasks_deferred.size(), source);
}

int server_queue::post(server_task && task, bool front) {
''')
modified[p] = replace_once(modified[p], '    const int  task_id     = task.id;\n', '''    stamp_scheduled_task(task, ggml_time_ms());
    const int  task_id     = task.id;
''')
modified[p] = replace_once(modified[p], '        reset_timer |= task_resets_idle_timer(task.type);\n', '''        stamp_scheduled_task(task, ggml_time_ms());
        reset_timer |= task_resets_idle_timer(task.type);
''')
modified[p] = replace_once(modified[p], '    if (!queue_tasks_deferred.empty()) {\n        // try to find a task that uses the specified slot\n', '''    if (simple_scheduler_enabled) {
        const int64_t now_ms = ggml_time_ms();
        auto selected = select_scheduled_task(queue_tasks_deferred, now_ms, id_slot);
        if (selected != queue_tasks_deferred.end()) {
            log_scheduled_task(*selected, now_ms, "deferred", "original_fifo");
            queue_tasks.emplace_front(std::move(*selected));
            queue_tasks_deferred.erase(selected);
        }
    } else if (!queue_tasks_deferred.empty()) {
        // try to find a task that uses the specified slot
''')
modified[p] = replace_once(modified[p], '                queue_tasks.emplace_front(std::move(*it));\n', '''                log_scheduled_task(*it, ggml_time_ms(), "deferred", "original_slot");
                queue_tasks.emplace_front(std::move(*it));
''')
modified[p] = replace_once(modified[p], '            queue_tasks.emplace_front(std::move(queue_tasks_deferred.front()));\n', '''            log_scheduled_task(queue_tasks_deferred.front(), ggml_time_ms(), "deferred", "original_fifo");
            queue_tasks.emplace_front(std::move(queue_tasks_deferred.front()));
''')
modified[p] = replace_once(modified[p], '        server_task task = std::move(queue_tasks.front());\n        queue_tasks.pop_front();\n', '''        const int64_t now_ms = ggml_time_ms();
        auto selected = simple_scheduler_enabled ? select_scheduled_task(queue_tasks, now_ms) : queue_tasks.begin();
        log_scheduled_task(*selected, now_ms, "main", "original_fifo");
        server_task task = std::move(*selected);
        queue_tasks.erase(selected);
''')

p = 'tools/server/server-context.cpp'
modified[p] = replace_once(modified[p], '        // wiring up server queues\n', '''        bool simple_scheduler_enabled = false;
        int64_t background_aging_ms = 5000;
        try {
            if (const char * value = getenv("LLAMA_SERVER_SIMPLE_SCHEDULER")) {
                const std::string mode(value);
                if (mode != "0" && mode != "1") {
                    throw std::invalid_argument("LLAMA_SERVER_SIMPLE_SCHEDULER must be 0 or 1");
                }
                simple_scheduler_enabled = mode == "1";
            }
            if (const char * value = getenv("LLAMA_SERVER_BACKGROUND_AGING_MS")) {
                size_t parsed = 0;
                background_aging_ms = std::stoll(value, &parsed);
                if (parsed != std::string(value).size() || background_aging_ms < 1 || background_aging_ms > 3600000) {
                    throw std::invalid_argument("LLAMA_SERVER_BACKGROUND_AGING_MS must be 1..3600000");
                }
            }
        } catch (const std::exception & e) {
            SRV_ERR("invalid simple scheduler settings: %s\\n", e.what());
            return false;
        }
        queue_tasks.configure_simple_scheduler(simple_scheduler_enabled, background_aging_ms);
        SRV_INF("SCHED_CONFIG mode=%d background_aging_ms=%lld preemptive=0\\n", simple_scheduler_enabled, (long long) background_aging_ms);

        // wiring up server queues
''')
modified[p] = replace_once(modified[p], '        SLT_INF(slot, "processing task, is_child = %d\\n", slot.task->is_child());\n', '''        const int64_t launch_ms = ggml_time_ms();
        SLT_INF(slot, "SCHED_LAUNCH id=%d clientid=%s class=%s wait_ms=%lld enqueue_ms=%lld launch_ms=%lld mode=%d\\n",
                slot.task->id, slot.task->schedule_client_req_id.empty() ? "-" : slot.task->schedule_client_req_id.c_str(),
                slot.task->schedule_background ? "background" : "interactive", (long long) (launch_ms - slot.task->schedule_enqueued_ms),
                (long long) slot.task->schedule_enqueued_ms, (long long) launch_ms, queue_tasks.simple_scheduler_active());
        SLT_INF(slot, "processing task, is_child = %d\\n", slot.task->is_child());
''')
modified[p] = replace_once(modified[p], '            task.id_slot = json_value(data, "id_slot", -1);\n', '''            const std::string request_class = json_value(data, "request_class", std::string("interactive"));
            if (request_class != "interactive" && request_class != "background") {
                throw std::invalid_argument("request_class must be interactive or background");
            }
            task.schedule_background = request_class == "background";
            task.schedule_client_req_id = json_value(data, "client_req_id", std::string());
            if (task.schedule_client_req_id.size() > 96 ||
                    task.schedule_client_req_id.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:-") != std::string::npos) {
                throw std::invalid_argument("client_req_id must contain at most 96 ASCII letters, digits, or _.:-");
            }
            task.id_slot = json_value(data, "id_slot", -1);
''')

patch = []
for name in FILES:
    dest = OUT / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(modified[name], encoding='utf-8', newline='\n')
    patch.extend(difflib.unified_diff(sources[name].splitlines(keepends=True),
                                    modified[name].splitlines(keepends=True),
                                    fromfile='a/' + name, tofile='b/' + name))
(ROOT / 'patches/simple-priority-aging-v1.patch').write_text(''.join(patch), encoding='utf-8', newline='\n')
print('Generated', len(patch), 'diff lines for', len(FILES), 'files')
