"""
技能工具 - Agent 按需加载技能的工具

实现渐进式披露（Level 2）：在需要时加载完整的技能内容
"""

from typing import Any, Dict, List, Optional

from .base import Tool, ToolResult
from .skill_loader import SkillLoader


class GetSkillTool(Tool):
    """获取特定技能详细信息的工具"""

    def __init__(self, skill_loader: SkillLoader):
        self.skill_loader = skill_loader

    @property
    def name(self) -> str:
        return "get_skill"

    @property
    def description(self) -> str:
        return "获取指定技能的完整内容和指导，用于执行特定类型的任务"

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "要检索的技能名称（使用 list_skills 查看可用技能）",
                }
            },
            "required": ["skill_name"],
        }

    async def execute(self, skill_name: str) -> ToolResult:
        """获取指定技能的详细信息"""
        skill = self.skill_loader.get_skill(skill_name)

        if not skill:
            available = ", ".join(self.skill_loader.list_skills())
            return ToolResult(
                success=False,
                content="",
                error=f"技能 '{skill_name}' 不存在。可用技能: {available}",
            )

        # 返回完整的技能内容
        result = skill.to_prompt()
        return ToolResult(success=True, content=result)


def create_skill_tools(
    skills_dir: str = "./skills",
) -> tuple[List[Tool], Optional[SkillLoader]]:
    """
    为渐进式披露创建技能工具

    仅提供 get_skill 工具 - Agent 使用系统 prompt 中的元数据
    了解有哪些技能可用，然后按需加载它们。

    Args:
        skills_dir: 技能目录路径

    Returns:
        （工具列表，技能加载器）的元组
    """
    # 创建技能加载器
    loader = SkillLoader(skills_dir)

    # 发现并加载技能
    skills = loader.discover_skills()
    print(f"✅ 已发现 {len(skills)} 个 Claude Skills")

    # 仅创建 get_skill 工具（渐进式披露 Level 2）
    tools = [
        GetSkillTool(loader),
    ]

    return tools, loader
