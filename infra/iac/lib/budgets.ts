import * as budgets from "aws-cdk-lib/aws-budgets";
import { Construct } from "constructs";

export function createCostBudget(
  scope: Construct,
  id: string,
  amountUsd: number,
  email: string,
): budgets.CfnBudget {
  return new budgets.CfnBudget(scope, id, {
    budget: {
      budgetLimit: { amount: amountUsd, unit: "USD" },
      budgetName: id,
      budgetType: "COST",
      timeUnit: "MONTHLY",
    },
    notificationsWithSubscribers: [80, 100].map((threshold) => ({
      notification: {
        comparisonOperator: "GREATER_THAN",
        notificationType: "ACTUAL",
        threshold,
        thresholdType: "PERCENTAGE",
      },
      subscribers: [{ address: email, subscriptionType: "EMAIL" }],
    })),
  });
}
