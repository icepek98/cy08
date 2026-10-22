import inspect
import threading
import unittest
from typing import Mapping

import automation
from automation import (
    AutomationExecutor,
    AutomationRunner,
    DemoAutomationExecutor,
    ProgressCallback,
)


class FakeAutomationRunner:
    def run(
        self,
        records: list[Mapping[str, object]] | None = None,
        progress_callback: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[dict[str, object]]:
        return [{"status": "SUCCESS"}]


def execute_with_runner(runner: AutomationRunner) -> list[dict[str, object]]:
    return runner.run(records=[])


class AutomationExecutorTests(unittest.TestCase):
    def test_demo_executor_exists(self) -> None:
        self.assertTrue(callable(DemoAutomationExecutor))

    def test_legacy_executor_is_demo_executor_alias(self) -> None:
        self.assertIs(AutomationExecutor, DemoAutomationExecutor)

    def test_run_signature_remains_compatible(self) -> None:
        signature = inspect.signature(AutomationExecutor.run)
        self.assertEqual(
            list(signature.parameters),
            ["self", "records", "progress_callback", "cancel_event"],
        )
        self.assertEqual(
            [parameter.default for parameter in signature.parameters.values()],
            [inspect.Parameter.empty, None, None, None],
        )
        self.assertEqual(
            signature.return_annotation, list[dict[str, object]]
        )
        self.assertEqual(signature, inspect.signature(AutomationRunner.run))
        self.assertEqual(signature, inspect.signature(DemoAutomationExecutor.run))

    def test_fake_runner_satisfies_protocol_without_playwright(self) -> None:
        result = execute_with_runner(FakeAutomationRunner())
        self.assertEqual(result, [{"status": "SUCCESS"}])

    def test_real_executor_is_not_introduced(self) -> None:
        self.assertFalse(hasattr(automation, "RealAutomationExecutor"))


if __name__ == "__main__":
    unittest.main()
