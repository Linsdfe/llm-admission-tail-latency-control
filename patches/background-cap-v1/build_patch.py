from pathlib import Path
import difflib

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = ROOT / 'third_party/llama.cpp'
SIMPLE = ROOT / 'patches/simple-priority-aging-v1'
FILES = ['tools/server/server-task.h', 'tools/server/server-queue.h',
         'tools/server/server-queue.cpp', 'tools/server/server-context.cpp']


def replace_once(text, old, new):
    assert text.count(old) == 1, (old[:100], text.count(old))
    return text.replace(old, new, 1)


modified = {name: (SIMPLE / name).read_text(encoding='utf-8') for name in FILES}
p = 'tools/server/server-queue.h'
modified[p] = replace_once(modified[p], '#include <unordered_set>\n', '#include <unordered_set>\n#include <unordered_map>\n')
modified[p] = replace_once(modified[p], '    int64_t background_aging_ms = 5000;\n', '''    int64_t background_aging_ms = 5000;
    int max_running_background = 0;
    struct running_task {
        int id;
        std::string client_id;
        bool background;
    };
    std::unordered_map<int, running_task> running_slots;
''')
modified[p] = replace_once(modified[p], 'void configure_simple_scheduler(bool enabled, int64_t aging_ms = 5000)', 'void configure_simple_scheduler(bool enabled, int64_t aging_ms = 5000, int background_cap = 0)')
modified[p] = replace_once(modified[p], '        background_aging_ms = aging_ms;\n', '''        GGML_ASSERT(background_cap == 0 || background_cap == 1);
        GGML_ASSERT(enabled || background_cap == 0);
        background_aging_ms = aging_ms;
        max_running_background = background_cap;
''')
modified[p] = replace_once(modified[p], '    bool simple_scheduler_active() const { return simple_scheduler_enabled; }\n', '''    bool simple_scheduler_active() const { return simple_scheduler_enabled; }
    int background_cap() const { return max_running_background; }

    // Called only after successful slot setup and after its original reset path.
    void notify_slot_launch(const server_task & task, int id_slot);
    void notify_slot_release(int id_slot);
''')
modified[p] = replace_once(modified[p], '    void cleanup_pending_task(int id_target);\n', '''    size_t running_background_count() const;
    bool background_blocked(const server_task & task) const;
    void defer_capped_tasks();
    void cleanup_pending_task(int id_target);
''')
p = 'tools/server/server-queue.cpp'
modified[p] = replace_once(modified[p], 'void server_queue::stamp_scheduled_task(', '''size_t server_queue::running_background_count() const {
    size_t count = 0;
    for (const auto & entry : running_slots) {
        count += entry.second.background;
    }
    return count;
}

bool server_queue::background_blocked(const server_task & task) const {
    return max_running_background > 0 && task.need_sampling() && task.schedule_background &&
           running_background_count() >= (size_t) max_running_background;
}

void server_queue::notify_slot_launch(const server_task & task, int id_slot) {
    std::unique_lock<std::mutex> lock(mutex_tasks);
    const size_t before = running_background_count();
    const bool background = task.need_sampling() && task.schedule_background;
    GGML_ASSERT(!background_blocked(task));
    GGML_ASSERT(running_slots.find(id_slot) == running_slots.end());
    running_slots.emplace(id_slot, running_task{task.id, task.schedule_client_req_id, background});
    QUE_INF("SCHED_CAP_LAUNCH id=%d clientid=%s class=%s slot=%d running_background_before=%zu running_background_after=%zu cap=%d event_ms=%lld\\n",
            task.id, task.schedule_client_req_id.empty() ? "-" : task.schedule_client_req_id.c_str(),
            background ? "background" : "interactive", id_slot, before, running_background_count(),
            max_running_background, (long long) ggml_time_ms());
}

void server_queue::notify_slot_release(int id_slot) {
    std::unique_lock<std::mutex> lock(mutex_tasks);
    auto found = running_slots.find(id_slot);
    if (found == running_slots.end()) {
        return;
    }
    const size_t before = running_background_count();
    const running_task task = found->second;
    running_slots.erase(found);
    QUE_INF("SCHED_CAP_RELEASE id=%d clientid=%s class=%s slot=%d running_background_before=%zu running_background_after=%zu cap=%d event_ms=%lld\\n",
            task.id, task.client_id.empty() ? "-" : task.client_id.c_str(),
            task.background ? "background" : "interactive", id_slot, before, running_background_count(),
            max_running_background, (long long) ggml_time_ms());
    condition_tasks.notify_all();
}

void server_queue::defer_capped_tasks() {
    // Caller holds mutex_tasks. Move each blocked task once; no retry spin.
    for (auto it = queue_tasks.begin(); it != queue_tasks.end();) {
        if (!background_blocked(*it)) {
            ++it;
            continue;
        }
        QUE_INF("SCHED_CAP_DEFER id=%d clientid=%s class=background running_background=%zu cap=%d event_ms=%lld\\n",
                it->id, it->schedule_client_req_id.empty() ? "-" : it->schedule_client_req_id.c_str(),
                running_background_count(), max_running_background, (long long) ggml_time_ms());
        queue_tasks_deferred.push_back(std::move(*it));
        it = queue_tasks.erase(it);
    }
}

void server_queue::stamp_scheduled_task(''')
modified[p] = replace_once(modified[p], '    for (auto it = tasks.begin(); it != tasks.end(); ++it) {\n', '''    for (auto it = tasks.begin(); it != tasks.end(); ++it) {
        if (background_blocked(*it)) {
            continue;
        }
''')
modified[p] = replace_once(modified[p], '        if (queue_tasks.empty()) {\n            return false;\n        }\n', '''        defer_capped_tasks();
        if (queue_tasks.empty()) {
            return false;
        }
''')
modified[p] = replace_once(modified[p], '''        server_task task = std::move(*selected);
        queue_tasks.erase(selected);
        lock.unlock();
''', '''        server_task task = std::move(*selected);
        queue_tasks.erase(selected);
        if (task.type == SERVER_TASK_TYPE_CANCEL) {
            cleanup_pending_task(task.id_target);
        }
        lock.unlock();
''')
p = 'tools/server/server-context.cpp'
modified[p] = replace_once(modified[p], '            slot.callback_on_release = [this](int id_slot) {\n', '''            slot.callback_on_release = [this](int id_slot) {
                queue_tasks.notify_slot_release(id_slot);
''')
modified[p] = replace_once(modified[p], '        int64_t background_aging_ms = 5000;\n', '        int64_t background_aging_ms = 5000;\n        int max_running_background = 0;\n')
modified[p] = replace_once(modified[p], '            if (const char * value = getenv("LLAMA_SERVER_BACKGROUND_AGING_MS")) {\n', '''            if (const char * value = getenv("LLAMA_SERVER_MAX_RUNNING_BACKGROUND")) {
                const std::string cap(value);
                if (cap != "0" && cap != "1") {
                    throw std::invalid_argument("LLAMA_SERVER_MAX_RUNNING_BACKGROUND must be 0 or 1");
                }
                max_running_background = cap == "1" ? 1 : 0;
            }
            if (max_running_background && !simple_scheduler_enabled) {
                throw std::invalid_argument("background cap requires simple scheduler mode 1");
            }
            if (const char * value = getenv("LLAMA_SERVER_BACKGROUND_AGING_MS")) {
''')
modified[p] = replace_once(modified[p], '''        queue_tasks.configure_simple_scheduler(simple_scheduler_enabled, background_aging_ms);
        SRV_INF("SCHED_CONFIG mode=%d background_aging_ms=%lld preemptive=0\\n", simple_scheduler_enabled, (long long) background_aging_ms);
''', '''        queue_tasks.configure_simple_scheduler(simple_scheduler_enabled, background_aging_ms, max_running_background);
        SRV_INF("SCHED_CONFIG mode=%d background_aging_ms=%lld preemptive=0 max_running_background=%d\\n",
                simple_scheduler_enabled, (long long) background_aging_ms, max_running_background);
''')
modified[p] = replace_once(modified[p], '        const int64_t launch_ms = ggml_time_ms();\n', '''        queue_tasks.notify_slot_launch(*slot.task, slot.id);
        const int64_t launch_ms = ggml_time_ms();
''')
modified[p] = replace_once(modified[p], '                    if (task.is_parent()) {\n', '''                    if (task.is_parent() && task.schedule_background && queue_tasks.background_cap() > 0) {
                        send_error(task, "Capped background requests support n=1 only", ERROR_TYPE_INVALID_REQUEST);
                        queue_tasks.pop_deferred_task(slot->id);
                        break;
                    }

                    if (task.is_parent()) {
''')
modified[p] = replace_once(modified[p], '''                    } else if (!launch_slot_with_task(*slot, std::move(task))) {
                        SRV_ERR("failed to launch slot with task, id_task = %d\\n", id_task);
                        break; // drop the task
''', '''                    } else if (!launch_slot_with_task(*slot, std::move(task))) {
                        SRV_ERR("failed to launch slot with task, id_task = %d\\n", id_task);
                        if (queue_tasks.background_cap() > 0) {
                            queue_tasks.pop_deferred_task(slot->id);
                        }
                        break; // drop the task
''')
modified[p] = replace_once(modified[p], '''            case SERVER_TASK_TYPE_CANCEL:
                {
                    // release slot linked with the task id
                    for (auto & slot : slots) {
                        if (slot.task && slot.task->id == task.id_target) {
                            slot.release();
                            break;
                        }
                    }
                } break;
''', '''            case SERVER_TASK_TYPE_CANCEL:
                {
                    bool released_active = false;
                    // release slot linked with the task id
                    for (auto & slot : slots) {
                        if (slot.task && slot.task->id == task.id_target) {
                            released_active = slot.is_processing();
                            slot.release();
                            break;
                        }
                    }
                    if (!released_active && queue_tasks.background_cap() > 0) {
                        // A queued cancellation can remove a task just promoted by release.
                        for (auto & slot : slots) {
                            if (!slot.is_processing()) {
                                queue_tasks.pop_deferred_task(slot.id);
                            }
                        }
                    }
                } break;
''')

diff = []
for name in FILES:
    target = OUT / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(modified[name], encoding='utf-8', newline='\n')
    old = (BASE / name).read_text(encoding='utf-8')
    diff.extend(difflib.unified_diff(old.splitlines(True), modified[name].splitlines(True),
                                   fromfile='a/' + name, tofile='b/' + name))
(OUT.parent / 'background-cap-v1.patch').write_text(''.join(diff), encoding='utf-8', newline='\n')
print('wrote four full source copies and background-cap-v1.patch')
