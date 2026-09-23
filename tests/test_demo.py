from personal_algorithm.demo import run_demo


def test_demo_is_ranked_and_fully_explained():
    output = run_demo()

    assert len(output) == 2
    assert output[0]["score"] >= output[1]["score"]
    for item in output:
        assert item["score"] == item["explanation"]["final_score"]
        assert item["explanation"]["contributions"]
