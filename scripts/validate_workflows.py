#!/usr/bin/env python3
"""
GitHub Actions 工作流程驗證腳本

此腳本驗證 GitHub Actions 工作流程文件的語法和配置正確性。
"""

import sys
from pathlib import Path

import yaml


def validate_yaml_syntax(file_path: Path) -> bool:
    """驗證 YAML 文件語法"""
    try:
        with open(file_path, encoding="utf-8") as f:
            yaml.safe_load(f)
        print(f"✅ {file_path.name}: YAML 語法正確")
        return True
    except yaml.YAMLError as e:
        print(f"❌ {file_path.name}: YAML 語法錯誤 - {e}")
        return False
    except Exception as e:
        print(f"❌ {file_path.name}: 讀取文件失敗 - {e}")
        return False


def validate_workflow_structure(file_path: Path) -> bool:
    """驗證工作流程結構"""
    try:
        with open(file_path, encoding="utf-8") as f:
            workflow = yaml.safe_load(f)

        if workflow is None:
            print(f"❌ {file_path.name}: 文件為空或解析失敗")
            return False

        required_fields = ["name", "jobs"]
        for field in required_fields:
            if field not in workflow:
                print(f"❌ {file_path.name}: 缺少必需字段 '{field}'")
                print(f"   實際字段: {list(workflow.keys())}")
                return False

        # 'on' 可能被 YAML 解析為 True
        if "on" not in workflow and True not in workflow:
            print(f"❌ {file_path.name}: 缺少觸發條件 'on'")
            print(f"   實際字段: {list(workflow.keys())}")
            return False

        if not isinstance(workflow["jobs"], dict):
            print(f"❌ {file_path.name}: 'jobs' 必須是字典")
            return False

        if not workflow["jobs"]:
            print(f"❌ {file_path.name}: 'jobs' 不能為空")
            return False

        print(f"✅ {file_path.name}: 工作流程結構正確")
        return True

    except Exception as e:
        print(f"❌ {file_path.name}: 結構驗證失敗 - {e}")
        return False


def validate_publish_workflow(file_path: Path) -> bool:
    """驗證發佈工作流程的特定配置"""
    try:
        with open(file_path, encoding="utf-8") as f:
            workflow = yaml.safe_load(f)

        on_section = workflow.get("on") or workflow.get(True)
        if not on_section:
            print(f"❌ {file_path.name}: 找不到觸發條件")
            return False

        workflow_dispatch = on_section.get("workflow_dispatch", {})
        inputs = workflow_dispatch.get("inputs", {})

        required_inputs = {"version_type"}
        actual_inputs = set(inputs.keys())

        if not required_inputs.issubset(actual_inputs):
            missing = required_inputs - actual_inputs
            print(f"❌ {file_path.name}: 缺少輸入參數: {missing}")
            print(f"   實際輸入參數: {actual_inputs}")
            return False

        print(f"✅ {file_path.name}: 發佈工作流程配置正確")
        return True

    except Exception as e:
        print(f"❌ {file_path.name}: 發佈工作流程驗證失敗 - {e}")
        return False


def main():
    """主函數"""
    print("🔍 驗證 GitHub Actions 工作流程...")
    print()

    workflows_dir = Path(__file__).parent.parent / ".github" / "workflows"

    if not workflows_dir.exists():
        print(f"❌ 工作流程目錄不存在: {workflows_dir}")
        sys.exit(1)

    workflow_files = list(workflows_dir.glob("*.yml")) + list(
        workflows_dir.glob("*.yaml")
    )

    if not workflow_files:
        print(f"❌ 在 {workflows_dir} 中沒有找到工作流程文件")
        sys.exit(1)

    print(f"📁 找到 {len(workflow_files)} 個工作流程文件")
    print()

    all_valid = True

    for workflow_file in sorted(workflow_files):
        print(f"🔍 驗證 {workflow_file.name}...")

        if not validate_yaml_syntax(workflow_file):
            all_valid = False
            continue

        if not validate_workflow_structure(workflow_file):
            all_valid = False
            continue

        if workflow_file.name == "publish.yml":
            if not validate_publish_workflow(workflow_file):
                all_valid = False

        print()

    if all_valid:
        print("🎉 所有工作流程文件驗證通過！")
        print()
        print("📋 下一步:")
        print("  1. 提交並推送更改到 GitHub")
        print("  2. 測試 'Auto Release to PyPI' 工作流程")
    else:
        print("❌ 部分工作流程文件驗證失敗")
        print("請修復上述問題後重新運行驗證")
        sys.exit(1)


if __name__ == "__main__":
    main()
