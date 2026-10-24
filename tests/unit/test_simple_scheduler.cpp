#include "server-queue.h"

#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

static server_task request(int id, bool background, int64_t queued_ms, int id_slot = -1) {
    server_task task(SERVER_TASK_TYPE_COMPLETION);
    task.id = id;
    task.id_slot = id_slot;
    task.schedule_background = background;
    task.schedule_enqueued_ms = queued_ms;
    task.schedule_client_req_id = "test-" + std::to_string(id);
    return task;
}

static std::vector<int> drain(server_queue & queue) {
    std::vector<int> ids;
    queue.on_new_task([&](server_task && task, bool yielding) {
        if (yielding) {
            throw std::runtime_error("unexpected yield");
        }
        ids.push_back(task.id);
        return true;
    });
    queue.on_update_slots([&]() { queue.terminate(); });
    queue.start_loop();
    return ids;
}

static void expect(const char * name, const std::vector<int> & actual, const std::vector<int> & expected) {
    if (actual != expected) {
        std::cerr << "FAIL " << name << ":";
        for (int id : actual) {
            std::cerr << " " << id;
        }
        std::cerr << "\n";
        throw std::runtime_error(name);
    }
    std::cout << "PASS " << name << "\n";
}

int main() {
    const int64_t now_ms = ggml_time_ms();
    {
        server_queue queue;
        queue.configure_simple_scheduler(false);
        queue.post(request(1, true, now_ms));
        queue.post(request(2, false, now_ms));
        expect("disabled preserves FIFO", drain(queue), {1, 2});
    }
    {
        server_queue queue;
        queue.configure_simple_scheduler(true);
        queue.post(request(1, true, now_ms));
        queue.post(request(2, false, now_ms));
        expect("interactive before unaged background", drain(queue), {2, 1});
    }
    {
        server_queue queue;
        queue.configure_simple_scheduler(true);
        queue.post(request(1, true, now_ms - 6000));
        queue.post(request(2, false, now_ms));
        expect("aged background promoted", drain(queue), {1, 2});
    }
    {
        server_queue queue;
        queue.configure_simple_scheduler(true);
        queue.post(request(1, true, now_ms - 6000));
        queue.post(request(2, false, now_ms));
        server_task cancel(SERVER_TASK_TYPE_CANCEL);
        cancel.id = 3;
        queue.post(std::move(cancel));
        expect("control before inference", drain(queue), {3, 1, 2});
    }
    {
        server_queue queue;
        queue.configure_simple_scheduler(true);
        queue.defer(request(1, true, now_ms, 0));
        queue.defer(request(2, false, now_ms));
        queue.pop_deferred_task(0);
        expect("slot affinity cannot bypass priority", drain(queue), {2});
    }
    {
        server_queue queue;
        queue.configure_simple_scheduler(false);
        queue.defer(request(1, false, now_ms));
        queue.defer(request(2, true, now_ms, 0));
        queue.pop_deferred_task(0);
        expect("disabled preserves slot affinity", drain(queue), {2});
    }
    {
        server_queue queue;
        queue.configure_simple_scheduler(true);
        queue.defer(request(1, true, now_ms - 6000, 1));
        queue.defer(request(2, false, now_ms, 0));
        queue.pop_deferred_task(0);
        expect("pinned busy slot skipped", drain(queue), {2});
    }
    {
        server_queue queue;
        queue.configure_simple_scheduler(true);
        queue.post(request(1, false, now_ms));
        queue.defer(request(2, true, now_ms));
        server_task cancel_main(SERVER_TASK_TYPE_CANCEL);
        cancel_main.id = 3;
        cancel_main.id_target = 1;
        queue.post(std::move(cancel_main));
        server_task cancel_deferred(SERVER_TASK_TYPE_CANCEL);
        cancel_deferred.id = 4;
        cancel_deferred.id_target = 2;
        queue.post(std::move(cancel_deferred));
        queue.pop_deferred_task(0);
        expect("cancel removes main and deferred tasks", drain(queue), {3, 4});
    }
    {
        server_queue queue;
        queue.configure_simple_scheduler(true);
        queue.post(request(1, true, now_ms - 6000));
        queue.post(request(2, false, now_ms));
        bool deferred = false;
        std::vector<int> ids;
        queue.on_new_task([&](server_task && task, bool) {
            if (task.id == 1 && !deferred) {
                deferred = true;
                queue.defer(std::move(task));
                queue.pop_deferred_task(0);
            } else {
                ids.push_back(task.id);
            }
            return true;
        });
        queue.on_update_slots([&]() { queue.terminate(); });
        queue.start_loop();
        expect("deferral retains original age", ids, {1, 2});
    }
    std::cout << "9 scheduler checks passed\n";
    return EXIT_SUCCESS;
}
