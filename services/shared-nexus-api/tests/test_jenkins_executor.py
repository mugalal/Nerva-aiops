from unittest.mock import patch, Mock

from app.remediation.jenkins_executor import trigger_jenkins_remediation


def test_trigger_jenkins_remediation():
    mock_response = Mock()
    mock_response.status_code = 201
    mock_response.headers = {
        "Location": "http://localhost:8080/queue/item/123/"
    }

    with patch(
        "app.remediation.jenkins_executor.JENKINS_URL",
        "http://localhost:8080"
    ), patch(
        "app.remediation.jenkins_executor.JENKINS_USER",
        "fake-user"
    ), patch(
        "app.remediation.jenkins_executor.JENKINS_API_TOKEN",
        "fake-token"
    ), patch(
        "app.remediation.jenkins_executor.requests.post",
        return_value=mock_response
    ), patch(
        "app.remediation.jenkins_executor.wait_for_jenkins_result",
        return_value={
            "success": True,
            "result": "SUCCESS",
            "build_url": "http://localhost:8080/job/nerva-m4-remediation/12/",
        }
    ):

        result = trigger_jenkins_remediation(
            "SCALE",
            4
        )

    assert result["success"] is True
    assert result["result"] == "SUCCESS"