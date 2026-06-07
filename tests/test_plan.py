from orchestrator.schemas.architect_output import Task


def test_defaults():
    # Test that new fields default to None
    task = Task(
        task_id="T1",
        title="Test Task",
        description="Description",
        files_to_modify=["test.py"],
        priority="low",
        effort="low",
        risk_level="low",
    )
    assert task.reason is None
    assert task.risk_reasons is None
    assert task.validation_expectations is None
    assert task.status is None


def test_serialization():
    # Test that new fields serialize correctly
    task = Task(
        task_id="T1",
        title="Test Task",
        description="Description",
        files_to_modify=["test.py"],
        priority="low",
        effort="low",
        risk_level="high",
        reason="Because",
        risk_reasons=["High complexity"],
        validation_expectations=["Test passes"],
    )
    data = task.model_dump()
    assert data["reason"] == "Because"
    assert data["risk_reasons"] == ["High complexity"]
    assert data["validation_expectations"] == ["Test passes"]
    assert "status" in data  # Pydantic includes it even if None by default


def test_status_assignment():
    # Logic test mimicking plan.py post-Architect status assignment
    tasks = [
        Task(
            task_id="T1",
            title="High risk",
            description="...",
            files_to_modify=["f.py"],
            priority="high",
            effort="high",
            risk_level="high",
        ),
        Task(
            task_id="T2",
            title="Low risk",
            description="...",
            files_to_modify=["f.py"],
            priority="low",
            effort="low",
            risk_level="low",
        ),
    ]

    # Simulate plan.py post-processing logic
    new_plan = []
    for plan_task in tasks:
        task_dict = plan_task.model_dump()
        if plan_task.risk_level == "high":
            task_dict["status"] = "blocked"
        new_plan.append(Task(**task_dict))

    assert new_plan[0].status == "blocked"
    assert new_plan[1].status is None
