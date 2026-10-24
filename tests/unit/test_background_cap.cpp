#include "server-queue.h"

#include <cstdlib>
#include <chrono>
#include <future>
#include <iostream>
#include <stdexcept>
#include <vector>

static server_task request(int id, bool background, int64_t queued_ms) {
    server_task task(SERVER_TASK_TYPE_COMPLETION);
    task.id = id;
    task.schedule_background = background;
    task.schedule_enqueued_ms = queued_ms;
    task.schedule_client_req_id = "cap-test-" + std::to_string(id);
    return task;
}

static void check(bool ok, const char * message) {
    if (!ok) {
        throw std::runtime_error(message);
    }
    std::cout << "PASS " << message << "\n";
}

static void check_yield_cancel(bool enabled, int cap, const char * message) {
    server_queue queue;
    queue.configure_simple_scheduler(enabled, 5000, cap);
    std::promise<void> selected;
    std::promise<void> cancel_posted;
    auto selected_ready = selected.get_future();
    auto cancel_ready = cancel_posted.get_future();
    bool launched = false;
    bool cancel_processed = false;
    int phase = 0;
    server_task initial(SERVER_TASK_TYPE_METRICS);
    initial.id = 90;
    queue.post(std::move(initial));
    queue.on_new_task([&](server_task && task, bool yielding) {
        if (yielding) {
            if (task.id == 1) {
                selected.set_value();
                if (cancel_ready.wait_for(std::chrono::seconds(5)) != std::future_status::ready) {
                    throw std::runtime_error("cancel barrier timed out");
                }
            }
            return false;
        }
        if (task.id == 1) {
            launched = true;
            queue.notify_slot_launch(task, 0);
        }
        cancel_processed |= task.type == SERVER_TASK_TYPE_CANCEL;
        return true;
    });
    queue.on_update_slots([&]() {
        if (phase++ == 0) {
            queue.post(request(1, true, ggml_time_ms()));
            queue.yield_to_queue([&]() {
                if (selected_ready.wait_for(std::chrono::seconds(5)) != std::future_status::ready) {
                    throw std::runtime_error("selection barrier timed out");
                }
                server_task cancel(SERVER_TASK_TYPE_CANCEL);
                cancel.id = 2;
                cancel.id_target = 1;
                queue.post(std::move(cancel), true);
                cancel_posted.set_value();
            });
        } else {
            if (launched) {
                queue.notify_slot_release(0);
            }
            queue.terminate();
        }
    });
    queue.start_loop();
    check(!launched && cancel_processed, message);
}

int main() {
    const int64_t now_ms = ggml_time_ms();
    {
        server_queue queue;
        queue.configure_simple_scheduler(false, 5000, 0);
        queue.post(request(1, true, now_ms));
        queue.post(request(2, true, now_ms));
        std::vector<int> ids;
        queue.on_new_task([&](server_task && task, bool) {
            ids.push_back(task.id);
            queue.notify_slot_launch(task, task.id - 1);
            return true;
        });
        queue.on_update_slots([&]() {
            queue.notify_slot_release(0);
            queue.notify_slot_release(1);
            queue.terminate();
        });
        queue.start_loop();
        check(ids == std::vector<int>({1, 2}), "cap0 permits two background launches and preserves FIFO");
    }
    {
        server_queue queue;
        queue.configure_simple_scheduler(true, 5000, 1);
        queue.post(request(1, true, now_ms - 9000));
        queue.post(request(2, true, now_ms - 8000));
        queue.post(request(3, false, now_ms));
        server_task control(SERVER_TASK_TYPE_METRICS);
        control.id = 4;
        queue.post(std::move(control));
        std::vector<int> ids;
        int phase = 0;
        queue.on_new_task([&](server_task && task, bool) {
            ids.push_back(task.id);
            if (task.id == 1 || task.id == 2) {
                check(task.schedule_enqueued_ms == now_ms - (task.id == 1 ? 9000 : 8000), "deferral preserves enqueue age");
                queue.notify_slot_launch(task, 0);
            } else if (task.id == 3) {
                queue.notify_slot_launch(task, 1);
            }
            return true;
        });
        queue.on_update_slots([&]() {
            if (phase++ == 0) {
                check(ids == std::vector<int>({4, 1, 3}), "control and interactive bypass capped aged background");
                check(queue.queue_tasks_deferred_size() == 1, "blocked background deferred once");
                queue.notify_slot_release(1);
                queue.notify_slot_release(1);
                queue.pop_deferred_task(1);
                check(queue.queue_tasks_deferred_size() == 1, "interactive release and duplicate release do not return background credit");
                queue.notify_slot_release(0);
                queue.pop_deferred_task(0);
                check(queue.queue_tasks_deferred_size() == 0, "background release makes deferred background eligible");
            } else {
                queue.notify_slot_release(0);
                queue.terminate();
            }
        });
        queue.start_loop();
        check(ids == std::vector<int>({4, 1, 3, 2}), "cap never bypassed by aging and slot can be reused after cleanup");
    }
    {
        server_queue queue;
        queue.configure_simple_scheduler(true, 5000, 1);
        auto active = request(10, true, now_ms - 6000);
        queue.notify_slot_launch(active, 0);
        queue.post(request(1, true, now_ms - 6000));
        queue.defer(request(2, true, now_ms - 6000));
        server_task cancel_main(SERVER_TASK_TYPE_CANCEL);
        cancel_main.id = 3;
        cancel_main.id_target = 1;
        queue.post(std::move(cancel_main));
        server_task cancel_deferred(SERVER_TASK_TYPE_CANCEL);
        cancel_deferred.id = 4;
        cancel_deferred.id_target = 2;
        queue.post(std::move(cancel_deferred));
        queue.post(request(5, true, now_ms - 6000));
        std::vector<int> ids;
        int phase = 0;
        queue.on_new_task([&](server_task && task, bool) {
            ids.push_back(task.id);
            if (task.need_sampling()) {
                queue.notify_slot_launch(task, 0);
            }
            return true;
        });
        queue.on_update_slots([&]() {
            if (phase++ == 0) {
                check(ids == std::vector<int>({3, 4}), "queued cancellation does not release active background credit");
                check(queue.queue_tasks_deferred_size() == 1, "queued cancellation cleans main and deferred tasks");
                queue.notify_slot_release(0);
                queue.pop_deferred_task(0);
            } else {
                queue.notify_slot_release(0);
                queue.terminate();
            }
        });
        queue.start_loop();
        check(ids == std::vector<int>({3, 4, 5}), "running cancellation cleanup permits next background launch");
    }
    {
        server_queue queue;
        queue.configure_simple_scheduler(true, 5000, 1);
        auto active = request(10, true, now_ms - 6000);
        queue.notify_slot_launch(active, 0);
        queue.defer(request(1, true, now_ms - 6000));
        queue.defer(request(2, true, now_ms - 6000));
        std::vector<int> ids;
        int phase = 0;
        queue.on_new_task([&](server_task && task, bool) {
            ids.push_back(task.id);
            if (task.id == 1) {
                // Simulate the context's prelaunch rejection path on an idle slot.
                queue.pop_deferred_task(0);
            } else {
                queue.notify_slot_launch(task, 0);
            }
            return true;
        });
        queue.on_update_slots([&]() {
            if (phase++ == 0) {
                queue.notify_slot_release(0);
                queue.pop_deferred_task(0);
                check(queue.queue_tasks_deferred_size() == 1, "release promotes only the first deferred background");
            } else {
                queue.notify_slot_release(0);
                queue.terminate();
            }
        });
        queue.start_loop();
        check(ids == std::vector<int>({1, 2}), "prelaunch rejection promotes next eligible task without phantom credit");
    }
    {
        server_queue queue;
        queue.configure_simple_scheduler(true, 5000, 1);
        auto active = request(10, true, now_ms - 6000);
        queue.notify_slot_launch(active, 0);
        queue.defer(request(1, true, now_ms - 6000));
        queue.defer(request(2, true, now_ms - 6000));
        std::vector<int> ids;
        int phase = 0;
        queue.on_new_task([&](server_task && task, bool) {
            ids.push_back(task.id);
            if (task.type == SERVER_TASK_TYPE_CANCEL) {
                // Context sees no active target and retries work on its idle slot.
                queue.pop_deferred_task(0);
            } else {
                queue.notify_slot_launch(task, 0);
            }
            return true;
        });
        queue.on_update_slots([&]() {
            if (phase++ == 0) {
                queue.notify_slot_release(0);
                queue.pop_deferred_task(0);
                server_task cancel(SERVER_TASK_TYPE_CANCEL);
                cancel.id = 3;
                cancel.id_target = 1;
                queue.post(std::move(cancel), true);
                check(queue.queue_tasks_deferred_size() == 1, "promoted cancellation leaves next background deferred before recovery");
            } else {
                check(queue.queue_tasks_deferred_size() == 0, "idle-slot cancellation recovery drains the deferred request");
                queue.notify_slot_release(0);
                queue.terminate();
            }
        });
        queue.start_loop();
        check(ids == std::vector<int>({3, 2}), "cancel after promotion cannot strand valid background work");
    }
    check_yield_cancel(false, 0, "original mode rechecks selected cancellation before callback");
    check_yield_cancel(true, 0, "simple mode rechecks selected cancellation before callback");
    check_yield_cancel(true, 1, "capped mode rechecks selected cancellation before callback");
    std::cout << "Background cap checks passed\n";
    return EXIT_SUCCESS;
}
