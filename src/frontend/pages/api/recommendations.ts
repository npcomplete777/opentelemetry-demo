// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

import type { NextApiRequest, NextApiResponse } from 'next';
import InstrumentationMiddleware from '../../utils/telemetry/InstrumentationMiddleware';
import RecommendationsGateway from '../../gateways/rpc/Recommendations.gateway';
import { Empty, Product } from '../../protos/demo';
import ProductCatalogService from '../../services/ProductCatalog.service';

type TResponse = Product[] | Empty;

const handler = async ({ method, query }: NextApiRequest, res: NextApiResponse<TResponse>) => {
  switch (method) {
    case 'GET': {
      const { productIds = [], sessionId = '', currencyCode = '' } = query;
      const { productIds: productList } = await RecommendationsGateway.listRecommendations(
        sessionId as string,
        productIds as string[]
      );

      // VALIS fix: Replace N individual GetProduct gRPC calls with a single
      // batch ListProducts call. Previously this was:
      //   Promise.all(productList.slice(0, 4).map(id => ProductCatalogService.getProduct(id, ...)))
      // which generated 4 parallel GetProduct spans, each triggering a DB query.
      // Now uses getProductsByIds() for 1 gRPC call → 1 DB query.
      const recommendedProductList = await ProductCatalogService.getProductsByIds(
        productList.slice(0, 4),
        currencyCode as string
      );

      return res.status(200).json(recommendedProductList);
    }

    default: {
      return res.status(405).send('');
    }
  }
};

export default InstrumentationMiddleware(handler);
