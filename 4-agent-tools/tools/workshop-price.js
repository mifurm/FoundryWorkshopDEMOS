"use strict";

module.exports = async function (context, req) {
  const value = req.query.attendees;
  if (typeof value !== "string" || !/^[1-9]\d{0,2}$/.test(value)) {
    context.log.warn("Rejected invalid attendees query parameter.");
    context.res = {
      status: 400,
      body: { error: "attendees must be an integer from 1 to 999." },
    };
    return;
  }
  const attendees = Number(value);
  context.res = {
    status: 200,
    headers: { "Content-Type": "application/json" },
    body: {
      attendees,
      currency: "USD",
      pricePerAttendee: 25,
      total: attendees * 25,
      source: "workshop-price-tool-v1",
    },
  };
};
