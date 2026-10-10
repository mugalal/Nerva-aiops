import time
import m2_loader

dispatch_mod = m2_loader.module("dispatch")
Dispatcher = dispatch_mod.Dispatcher


def test_dispatcher_submits_and_executes():
    executed = []

    def handle(item):
        executed.append(item)

    dispatcher = Dispatcher(handle)
    dispatcher.submit("INC-1")
    dispatcher.submit("INC-2")
    # Repeated submit while pending is collapsed
    dispatcher.submit("INC-2")

    # Wait for thread to process
    time.sleep(0.1)
    assert "INC-1" in executed
    assert "INC-2" in executed
